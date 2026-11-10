from datetime import timedelta
from copy import deepcopy
from django.test import TestCase, override_settings
from django.utils import timezone
from .models import EAHeartbeat, EACommand, LearningTradeOutcome
from .profit_protection import protection_status, create_protection_commands
from ramon_pipeline.signalflow.profit_protection import ProfitProtection


@override_settings(RAMON_PROFIT_PROTECTION_INSTANCE='demo',RAMON_PROFIT_PROTECTION_LOGIN='123',
    RAMON_PROFIT_PROTECTION_SERVER='Demo',RAMON_DEMO_PROFIT_PROTECTION_ENABLED=True,
    RAMON_EA_COMMAND_CREATION_ENABLED=True, RAMON_EA_COMMAND_EXECUTION_ENABLED=True)
class ProfitProtectionTests(TestCase):
    def setUp(self):
        self.feed=EAHeartbeat.objects.create(instance_id='demo',account_login='123',symbol='XAUUSD_o',
            server_name='Demo',status='TELEMETRY_OK',terminal_connected=True,trade_allowed=True,
            payload=dict(account_trade_mode=0,server_name='Demo',quote_time=timezone.now().isoformat(),
                quote=dict(bid=111,ask=111.2,point=.01),symbol_info=dict(tick_size=.01,stops_level_points=0),
                positions=[dict(ticket='42',symbol='XAUUSD_o',type=0,price_open=100,sl=90,tp=112.5,volume=.02)]))
        self.original=EACommand.objects.create(command_id='original',instance_id='demo',account_login='123',
            symbol='XAUUSD_o',action='OPEN',state='EXECUTED',execution_authorized=True,decision_id='protect-decision',
            expires_at=timezone.now(),payload=dict(side='BUY',stop_loss=90,take_profit=112.5,volume=.02,server_name='Demo'),
            result=dict(broker_ticket='42'))

    def save(self):
        self.feed.save()

    def produce(self):
        create_protection_commands('demo','123')
        return EACommand.objects.filter(action='MODIFY_SLTP')

    def test_once_only_preserves_target_and_uses_original_risk(self):
        c=self.produce().get()
        self.assertEqual(c.payload['stop_loss'],100)
        self.assertIs(type(c.payload['position_ticket']),int)
        self.assertEqual(c.payload['position_ticket'],42)
        self.assertEqual(c.payload['take_profit'],112.5)
        self.assertEqual(c.payload['original_risk'],10)
        self.assertEqual(c.payload['calculation_owner'],'DJANGO')
        self.assertEqual(self.produce().count(),1)
        c.state='FAILED'; c.save()
        self.assertEqual(self.produce().count(),1)

    def test_bounded_recovery_for_confirmed_ticket_zero_format_failure(self):
        c=self.produce().get()
        old_id=c.command_id
        c.payload['position_ticket']='42'
        c.state='FAILED'
        c.result={'detail':'BROKER_MODIFY_0_unknown retcode 0','broker_ticket':'0'}
        c.save()
        self.feed.payload['quote'].update(bid=109,ask=109.2);self.save()
        self.assertEqual(self.produce().count(),1)
        self.feed.payload['quote'].update(bid=111,ask=111.2);self.save()
        self.assertEqual(self.produce().count(),2)
        recovered=EACommand.objects.exclude(command_id__in=['original',old_id]).get()
        self.assertIs(type(recovered.payload['position_ticket']),int)
        recovered.state='FAILED';recovered.result=c.result;recovered.save()
        self.assertEqual(self.produce().count(),2)
        self.assertEqual(protection_status(self.feed)['positions'][0]['state'],'COMMAND_FAILED')

    def test_tickets_outside_exact_numeric_wire_range_fail_closed(self):
        self.feed.payload['positions'][0]['ticket']=str(2**53)
        self.save()
        self.assertEqual(self.produce().count(),0)
        self.assertEqual(protection_status(self.feed)['positions'][0]['state'],'UNSUPPORTED_TICKET')

    def test_scope_prevents_activation_for_other_demo_accounts(self):
        with self.settings(RAMON_PROFIT_PROTECTION_LOGIN='other'):
            self.assertEqual(self.produce().count(),0)
            self.assertEqual(protection_status(self.feed)['state'],'OUTSIDE_CONFIGURED_DEMO_SCOPE')

    def test_short_uses_ask_and_never_loosens_existing_protection(self):
        self.original.payload.update(side='SELL',stop_loss=110,take_profit=87.5);self.original.save()
        p=self.feed.payload
        p['positions'][0].update(type=1,sl=110,tp=87.5)
        p['quote'].update(bid=89.9,ask=90.1);self.save()
        self.assertEqual(protection_status(self.feed)['positions'][0]['state'],'WAITING_FOR_1R')
        self.assertEqual(self.produce().count(),0)
        p['quote'].update(bid=89.7,ask=89.9);self.save()
        self.assertEqual(self.produce().get().payload['stop_loss'],100)
        p['positions'][0]['sl']=99;self.save()
        self.assertEqual(protection_status(self.feed)['positions'][0]['state'],'ALREADY_PROTECTED')

    def test_demo_identity_freshness_and_permissions_fail_closed(self):
        original=deepcopy(self.feed.payload)
        for key,value in [('account_trade_mode',2),('account_trade_mode',False),('server_name','Other'),
            ('quote_time',(timezone.now()-timedelta(minutes=2)).isoformat())]:
            self.feed.payload=deepcopy(original);self.feed.payload[key]=value;self.save()
            self.assertEqual(self.produce().count(),0)
        self.feed.payload=original;self.feed.trade_allowed=False;self.save()
        self.assertEqual(self.produce().count(),0)

    def test_no_manual_foreign_or_changed_positions(self):
        original=deepcopy(self.feed.payload)
        for key,value in [('ticket','99'),('type',1),('volume',.03),('tp',115),('sl',0),('price_open',float('nan'))]:
            self.feed.payload=deepcopy(original);self.feed.payload['positions'][0][key]=value
            # NaN is not valid JSON; exercise planner directly for that case.
            if key=='price_open':
                self.assertEqual(protection_status(self.feed)['positions'][0]['state'],'UNVERIFIED_POSITION')
            else:
                self.save();self.assertEqual(self.produce().count(),0)
        self.original.account_login='other';self.original.save()
        self.feed.payload=original;self.save()
        self.assertEqual(self.produce().count(),0)

    def test_each_gate_blocks_command_and_observation_is_read_only(self):
        for key in ['RAMON_DEMO_PROFIT_PROTECTION_ENABLED','RAMON_EA_COMMAND_CREATION_ENABLED','RAMON_EA_COMMAND_EXECUTION_ENABLED']:
            with self.settings(**{key:False}): self.assertEqual(self.produce().count(),0)
        self.assertEqual(protection_status(self.feed)['positions'][0]['state'],'READY')
        self.assertEqual(EACommand.objects.count(),1)
        response=self.client.get('/api/ea/v1/research/execution').json()
        self.assertEqual(response['profit_protection']['calculation_owner'],'DJANGO')
        self.assertEqual(EACommand.objects.count(),1)

    def test_broker_distance_and_current_profit_trigger(self):
        self.feed.payload['symbol_info']['stops_level_points']=1200;self.save()
        self.assertEqual(self.produce().count(),0)
        self.feed.payload['symbol_info']['stops_level_points']=0
        self.feed.payload['quote'].update(bid=109.99,ask=110.2);self.save()
        self.assertEqual(self.produce().count(),0)
        self.feed.payload['quote'].update(bid=110,ask=110.2);self.save()
        self.assertEqual(self.produce().count(),1)

    @override_settings(EA_BEARER_TOKEN='test-token')
    def test_authenticated_poll_delivers_django_change_and_ack_is_visible(self):
        import json
        headers={'HTTP_AUTHORIZATION':'Bearer test-token'}
        data={'instance_id':'demo','account_login':'123'}
        response=self.client.post('/api/ea/v1/commands/next',data=json.dumps(data),content_type='application/json',**headers)
        self.assertEqual(response.status_code,200)
        command=response.json()['command']
        self.assertEqual(command['action'],'MODIFY_SLTP')
        self.assertEqual(command['payload']['stop_loss'],100)
        data.update(command_id=command['command_id'],state='EXECUTED',result={'detail':'BROKER_MODIFY_10009_done','broker_ticket':'42'})
        ack=self.client.post('/api/ea/v1/commands/ack',data=json.dumps(data),content_type='application/json',**headers)
        self.assertEqual(ack.status_code,200,ack.content)
        LearningTradeOutcome.objects.create(trade_id='protected-outcome',decision_id='protect-decision',
            instance_id='demo',account_login='123',symbol='XAUUSD_o',realized_pnl=-.1,
            opened_at=timezone.now()-timedelta(hours=1),closed_at=timezone.now()+timedelta(seconds=1))
        status=protection_status(self.feed)
        self.assertEqual(status['recent_commands'][0]['closed_result']['net_pnl'],-.1)
        self.assertEqual(status['recent_commands'][0]['state'],'EXECUTED')
        self.assertEqual(status['recent_commands'][0]['result']['detail'],'BROKER_MODIFY_10009_done')
        again=self.client.post('/api/ea/v1/commands/next',data=json.dumps({'instance_id':'demo','account_login':'123'}),content_type='application/json',**headers)
        self.assertIsNone(again.json()['command'])

    def test_pure_rules_do_not_loosen_and_trailing_is_optional(self):
        rule=ProfitProtection()
        self.assertEqual(rule.stop(side='BUY',entry=100,original_risk=10,current_stop=102,favorable_price=111),102)
        self.assertEqual(rule.stop(side='SELL',entry=100,original_risk=10,current_stop=110,favorable_price=90),100)
        trail=ProfitProtection(trailing_trigger_r=1)
        self.assertEqual(trail.stop(side='BUY',entry=100,original_risk=10,current_stop=90,favorable_price=115),110)
        with self.assertRaises(ValueError): ProfitProtection(breakeven_trigger_r=0)
