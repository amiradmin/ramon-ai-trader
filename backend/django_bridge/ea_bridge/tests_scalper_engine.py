from datetime import datetime, timedelta, timezone as utc_timezone
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from .models import AccountDailyBaseline, EACommand, EAHeartbeat, ScalperControl, ScalpObservation
from .scalper_engine import MAGIC, open_scalp, close_scalp, allow_main_alongside_scalps, exposure_money, engine_cycle


@override_settings(RAMON_SCALP_EXECUTION_ENABLED=True, RAMON_EA_COMMAND_CREATION_ENABLED=True,
    RAMON_EA_COMMAND_EXECUTION_ENABLED=True, RAMON_SCALP_INSTANCE='demo',RAMON_SCALP_LOGIN='123',RAMON_SCALP_SERVER='Demo')
class IndependentScalperTests(TestCase):
    def setUp(self):
        self.now = datetime.fromtimestamp(int(timezone.now().timestamp())//60*60+10,utc_timezone.utc)
        self.main = dict(ticket='1',symbol='GOLD',type=0,volume=.1,price_open=100,sl=90,tp=120,
                         tick_size=.01,tick_value=1,magic=0)
        self.feed = EAHeartbeat.objects.create(instance_id='demo',account_login='123',server_name='Demo',symbol='GOLD',
            status='TELEMETRY_OK',terminal_connected=True,trade_allowed=True,payload=dict(account_trade_mode=0,
                account_margin_mode=2,execution_profile='ticket-magic-v1',server_name='Demo',broker_day='2026-11-10',
                quote_time=self.now.isoformat(),quote=dict(bid=100,ask=100.2,point=.01),
                account=dict(balance=10000,equity=10000),positions=[self.main],
                symbol_info=dict(tick_size=.01,tick_value=1,volume_step=.01,volume_min=.01,volume_max=100,stops_level_points=0)))
        EAHeartbeat.objects.filter(pk=self.feed.pk).update(last_seen=self.now)
        self.feed.refresh_from_db()
        AccountDailyBaseline.objects.create(account_login='123',server_name='Demo',broker_day='2026-11-10',
            opening_balance=10000,source_instance_id='demo')
        self.control = ScalperControl.objects.create(source='demo',account_login='123',server_name='Demo',symbol='GOLD',automatic=True)
        self.observation = ScalpObservation.objects.create(source='demo',account_login='123',server_name='Demo',symbol='GOLD',
            version='v1',signal_bar_time=self.now-timedelta(seconds=70),recorded_at=self.now,action='BUY',reason='TEST',
            evaluation_start=self.now+timedelta(seconds=50),evaluation_end=self.now+timedelta(minutes=5),status='WAITING',
            snapshot=dict(plan=dict(stop=95,target=107)))

    def test_scalp_opens_alongside_main_without_modifying_it_and_deduplicates(self):
        command, reason = open_scalp(self.feed,self.control,self.observation,now=self.now)
        self.assertEqual(reason,'QUEUED')
        self.assertEqual(command.payload['engine'],'SCALPER')
        self.assertEqual(command.payload['magic'],MAGIC)
        self.assertLessEqual(command.payload['reserved_risk_money'],10)
        self.assertEqual(self.feed.payload['positions'],[self.main])
        open_scalp(self.feed,self.control,self.observation,now=self.now)
        self.assertEqual(EACommand.objects.count(),1)
        self.assertGreater(exposure_money(self.feed),100)

    def own_scalp(self):
        c, _ = open_scalp(self.feed,self.control,self.observation,now=self.now)
        c.state='EXECUTED';c.result={'broker_ticket':'2'};c.completed_at=self.now-timedelta(minutes=6);c.save()
        pos=dict(self.main,ticket='2',magic=MAGIC,volume=c.payload['volume'],sl=95,tp=107)
        self.feed.payload['positions'].append(pos);self.feed.save()
        EAHeartbeat.objects.filter(pk=self.feed.pk).update(last_seen=self.now)
        self.feed.refresh_from_db()
        return c,pos

    def test_manual_close_targets_scalp_only_and_never_main(self):
        self.own_scalp()
        command,reason=close_scalp(feed=self.feed,ticket='1',now=self.now)
        self.assertIsNone(command);self.assertEqual(reason,'NOT_A_SCALPER_POSITION')
        command,reason=close_scalp(feed=self.feed,ticket='2',now=self.now)
        self.assertEqual(reason,'QUEUED')
        self.assertEqual(command.payload['position_ticket'],2)
        self.assertEqual(command.payload['magic'],MAGIC)
        again,_=close_scalp(feed=self.feed,ticket='2',now=self.now)
        self.assertEqual(EACommand.objects.filter(action='CLOSE').count(),1)

    def test_main_can_coexist_only_with_verified_scalp_tickets(self):
        c,pos=self.own_scalp()
        self.feed.payload['positions']=[pos]
        self.assertTrue(allow_main_alongside_scalps(self.feed))
        pos['magic']=0
        self.assertFalse(allow_main_alongside_scalps(self.feed))

    def test_netting_old_ea_and_shared_risk_limit_fail_closed(self):
        self.feed.payload['account_margin_mode']=0;self.feed.save()
        EAHeartbeat.objects.filter(pk=self.feed.pk).update(last_seen=self.now)
        self.assertEqual(open_scalp(self.feed,self.control,self.observation,now=self.now)[1],'HEDGING_TELEMETRY_REQUIRED')
        self.feed.payload['account_margin_mode']=2
        self.feed.payload['positions'][0]['volume']=.199;self.feed.save()
        EAHeartbeat.objects.filter(pk=self.feed.pk).update(last_seen=self.now)
        self.assertEqual(open_scalp(self.feed,self.control,self.observation,now=self.now)[1],'RISK_BUDGET_BELOW_MINIMUM_VOLUME')
        self.feed.payload['execution_profile']='';self.feed.save()
        EAHeartbeat.objects.filter(pk=self.feed.pk).update(last_seen=self.now)
        self.assertEqual(open_scalp(self.feed,self.control,self.observation,now=self.now)[1],'EA_109_REQUIRED')
        self.assertEqual(EACommand.objects.count(),0)

    def test_pause_stops_entries_but_time_exit_continues(self):
        self.own_scalp()
        self.control.automatic=False;self.control.save()
        with patch('ea_bridge.scalper_engine.timezone.now',return_value=self.now):
            engine_cycle()
        self.assertEqual(EACommand.objects.filter(action='OPEN').count(),1)
        self.assertEqual(EACommand.objects.get(action='CLOSE').payload['close_reason'],'FIVE_MINUTE_TIME_EXIT')

    def test_staff_and_csrf_required_for_manual_controls(self):
        self.assertEqual(self.client.get('/api/ea/v1/scalper').status_code,302)
        user=get_user_model().objects.create_user(username='operator',password='local-test',is_staff=True)
        self.client.force_login(user)
        response=self.client.get('/api/ea/v1/scalper')
        self.assertEqual(response.status_code,200)
        self.assertContains(response,'Independent scalper')
        from django.test import Client
        strict=Client(enforce_csrf_checks=True);strict.force_login(user)
        self.assertEqual(strict.post('/api/ea/v1/scalper',{'action':'start','scope':self.control.pk}).status_code,403)

    def test_account_scope_and_daily_loss_block_entries(self):
        self.control.account_login='other';self.control.save()
        self.assertEqual(open_scalp(self.feed,self.control,self.observation,now=self.now)[1],'CONTROL_SCOPE_MISMATCH')
        self.control.account_login='123';self.control.save()
        self.feed.payload['account']['equity']=9400;self.feed.save()
        EAHeartbeat.objects.filter(pk=self.feed.pk).update(last_seen=self.now)
        self.assertEqual(open_scalp(self.feed,self.control,self.observation,now=self.now)[1],'DAILY_LOSS_LIMIT')

    def test_main_producer_can_open_with_a_verified_scalp_already_open(self):
        _,pos=self.own_scalp()
        self.feed.payload['positions']=[pos];self.feed.save()
        EAHeartbeat.objects.filter(pk=self.feed.pk).update(last_seen=timezone.now())
        from .models import StrategyDecisionRecord,RiskVerdictRecord
        from .command_producer import create_open_command
        self.feed.payload['quote_time']=timezone.now().isoformat();self.feed.save()
        decision=StrategyDecisionRecord.objects.create(decision_id='main-parallel',symbol='GOLD',action='BUY',
            confidence=.8,model_version='main',features={'account_snapshot':{'balance':10000,'equity':10000}})
        verdict=RiskVerdictRecord.objects.create(decision_id=decision.decision_id,approved=True,reason_code='TEST',
            approved_volume='.1',stop_loss='95',take_profit='107')
        command,created,reason=create_open_command(decision=decision,verdict=verdict,instance_id='demo',
            account_login='123',server_name='Demo')
        self.assertTrue(created,reason)
        self.assertNotEqual(command.payload.get('engine'),'SCALPER')

    @override_settings(EA_BEARER_TOKEN='test-token')
    def test_closed_scalps_are_attributed_and_excluded_from_main_learning(self):
        from .models import LearningTradeOutcome
        from .learning_store import DjangoOutcomeStore
        command,_=self.own_scalp()
        response=self.client.post('/api/ea/v1/learning/outcomes',data={
            'trade_id':'123:Demo:2','instance_id':'demo','account_login':'123','symbol':'GOLD',
            'side':'BUY','command_id':command.command_id,'broker_ticket':'2','realized_pnl':1.2,
            'opened_at':(self.now-timedelta(minutes=6)).isoformat(),'closed_at':self.now.isoformat()},
            content_type='application/json',HTTP_AUTHORIZATION='Bearer test-token')
        self.assertEqual(response.status_code,201,response.content)
        self.assertEqual(LearningTradeOutcome.objects.get().tags['strategy_profile'],'SCALPER_M1_M5')
        self.assertEqual(DjangoOutcomeStore().all(),[])

    def test_pending_main_risk_reserves_headroom_for_both_engines(self):
        EACommand.objects.create(command_id='main-pending',instance_id='demo',account_login='123',symbol='GOLD',
            action='OPEN',state='PENDING',expires_at=self.now+timedelta(minutes=1),
            payload=dict(server_name='Demo',reserved_risk_money=100))
        command,reason=open_scalp(self.feed,self.control,self.observation,now=self.now)
        self.assertIsNone(command)
        self.assertEqual(reason,'RISK_BUDGET_BELOW_MINIMUM_VOLUME')

    def test_approved_demo_risk_allows_minimum_lot_without_exceeding_budget(self):
        AccountDailyBaseline.objects.filter(account_login='123').update(opening_balance=Decimal('2209.45'))
        self.feed.payload['account'].update(balance=2209.45,equity=2209.45)
        self.feed.payload['positions']=[]
        self.feed.payload['quote'].update(bid=4112.66,ask=4112.88)
        self.feed.save()
        EAHeartbeat.objects.filter(pk=self.feed.pk).update(last_seen=self.now)
        self.observation.pk=None
        self.observation.signal_bar_time-=timedelta(seconds=1)
        self.observation.action='SELL'
        self.observation.snapshot={'plan':{'stop':4114.93,'target':4109.82}}
        self.observation.save()
        command,reason=open_scalp(self.feed,self.control,self.observation,now=self.now)
        self.assertIsNone(command)
        self.assertEqual(reason,'RISK_BUDGET_BELOW_MINIMUM_VOLUME')
        self.control.risk_percent=Decimal('.15');self.control.save()
        command,reason=open_scalp(self.feed,self.control,self.observation,now=self.now)
        self.assertEqual(reason,'QUEUED')
        self.assertEqual(command.payload['volume'],.01)
        self.assertAlmostEqual(command.payload['reserved_risk_money'],2.42)
        self.assertLessEqual(command.payload['reserved_risk_money'],2209.45*.0015)

    def test_risk_above_approved_demo_ceiling_is_rejected(self):
        self.control.risk_percent=Decimal('.151');self.control.save()
        command,reason=open_scalp(self.feed,self.control,self.observation,now=self.now)
        self.assertIsNone(command)
        self.assertEqual(reason,'SCALPER_RISK_LIMIT')
