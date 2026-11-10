from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
import json
from threading import Barrier
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection, connections
from django.test import TestCase, TransactionTestCase, override_settings, skipUnlessDBFeature
from django.utils import timezone

from .command_producer import create_open_command
from .decision import _build_pipeline, evaluate_payload
from .models import (EAHeartbeat, EACommand, StrategyWorkerControl, StrategyDecisionRecord,
                     RiskVerdictRecord, DecisionThresholdConfig, DecisionEvaluation)
from .strategy_registry import STRATEGIES, owned_strategy_position
from .strategy_worker import close_strategy_position, worker_cycle


SETTINGS=dict(RAMON_STRATEGY_CONTAINERS_ENABLED=True,RAMON_STRATEGY_INSTANCE='demo',
    RAMON_STRATEGY_LOGIN='123',RAMON_STRATEGY_SERVER='Demo',RAMON_EA_COMMAND_CREATION_ENABLED=True,
    RAMON_EA_COMMAND_EXECUTION_ENABLED=True,RAMON_MARKET_SESSION_HOURS=None,
    RAMON_MARKET_SESSION_TIMEZONE=None,EA_BEARER_TOKEN='test-token')


def fixture():
    now=timezone.now()
    feed=EAHeartbeat.objects.create(instance_id='demo',account_login='123',server_name='Demo',symbol='XAUUSD_o',
        status='TELEMETRY_OK',terminal_connected=True,trade_allowed=True,payload=dict(server_name='Demo',
            account_trade_mode=0,account_margin_mode=2,execution_profile='ticket-magic-v1',quote_time=now.isoformat(),
            quote=dict(bid=100,ask=100.2,point=.01),account=dict(balance=10000,equity=10000),positions=[],
            symbol_info=dict(tick_size=.01,tick_value=1,volume_min=.01,volume_max=100,volume_step=.01,stops_level_points=0)))
    for setup in STRATEGIES:
        StrategyWorkerControl.objects.create(setup=setup,source='demo',account_login='123',server_name='Demo',
            symbol=feed.symbol,last_seen=now)
    return feed


def approved(setup, suffix=''):
    decision=StrategyDecisionRecord.objects.create(decision_id=setup+suffix,symbol='XAUUSD_o',action='BUY',
        confidence=.8,model_version='setup-container-v1',features=dict(strategy_setup=setup,engine='STRATEGY',
            account_snapshot=dict(balance=10000,equity=10000),risk_policy=dict(maximum_risk_percent=.1)))
    verdict=RiskVerdictRecord.objects.create(decision_id=decision.decision_id,approved=True,reason_code='TEST',
        approved_volume=Decimal('.01'),stop_loss=Decimal('95'),take_profit=Decimal('107'))
    return decision,verdict


def open_for(setup,suffix=''):
    decision,verdict=approved(setup,suffix)
    return create_open_command(decision=decision,verdict=verdict,instance_id='demo',account_login='123',server_name='Demo')


@override_settings(**SETTINGS)
class StrategyWorkerTests(TestCase):
    def setUp(self): self.feed=fixture()

    def test_independent_evaluation_runs_only_its_setup_and_keys_are_scoped(self):
        from .tests_playbook import DemoPlaybookApiTests
        payload=DemoPlaybookApiTests().payload()
        a=json.loads(evaluate_payload(payload,strategy_setup='TREND_PULLBACK',input_evidence={'schema_version': 1}).content)
        b=json.loads(evaluate_payload(payload,strategy_setup='BREAKOUT_RETEST').content)
        self.assertEqual(len(a['selection']['candidates']),1)
        self.assertEqual(a['selection']['candidates'][0]['setup'],'TREND_PULLBACK')
        self.assertEqual(b['selection']['candidates'][0]['setup'],'BREAKOUT_RETEST')
        self.assertNotEqual(a['sample_key'],b['sample_key'])
        for result,setup in [(a,'TREND_PULLBACK'),(b,'BREAKOUT_RETEST')]:
            row=DecisionEvaluation.objects.get(pk=result['evaluation_id'])
            self.assertEqual(row.features['strategy_setup'],setup)
        self.assertIn('effective_policy', DecisionEvaluation.objects.get(pk=a['evaluation_id']).features)

    def test_each_pipeline_enables_exactly_one_setup(self):
        for setup in STRATEGIES:
            pipeline=_build_pipeline(demo_mode=True,strategy_setup=setup)
            enabled=[name for name,c in pipeline.opportunities.trading_settings['opportunity_finder']['setups'].items() if c['enabled']]
            self.assertEqual(enabled,[setup.lower()])
        with self.assertRaises(ValueError): _build_pipeline(demo_mode=True,strategy_setup='UNKNOWN')

    def test_independent_commands_magic_ownership_and_repeat_dedup(self):
        a,created,reason=open_for('TREND_PULLBACK')
        self.assertTrue(created,reason)
        b,created,reason=open_for('BREAKOUT_RETEST')
        self.assertTrue(created,reason)
        self.assertNotEqual(a.payload['magic'],b.payload['magic'])
        decision=StrategyDecisionRecord.objects.get(decision_id='TREND_PULLBACK')
        verdict=RiskVerdictRecord.objects.get(decision_id=decision.decision_id)
        repeated,created,_=create_open_command(decision=decision,verdict=verdict,
            instance_id='demo',account_login='123',server_name='Demo')
        self.assertFalse(created);self.assertEqual(repeated.pk,a.pk)
        self.assertEqual(EACommand.objects.count(),2)
        a.state='EXECUTED';a.result={'broker_ticket':'42'};a.completed_at=timezone.now()-timedelta(seconds=5);a.save()
        pos=dict(ticket='42',symbol='XAUUSD_o',magic=STRATEGIES['TREND_PULLBACK'][2],type=0,
            price_open=100.2,sl=95,tp=107,volume=.01,tick_size=.01,tick_value=1)
        self.feed.payload['positions']=[pos];self.feed.save()
        self.assertEqual(owned_strategy_position(self.feed,pos,setup='TREND_PULLBACK').pk,a.pk)
        self.assertIsNone(owned_strategy_position(self.feed,pos,setup='BREAKOUT_RETEST'))
        self.assertEqual(open_for('TREND_PULLBACK','next')[2],'STRATEGY_POSITION_ALREADY_OPEN')
        self.assertIsNotNone(open_for('EXHAUSTION_REVERSAL')[0])
        self.assertEqual(close_strategy_position('BREAKOUT_RETEST','42')[1],'NOT_OWNED_BY_THIS_STRATEGY')
        closed,reason=close_strategy_position('TREND_PULLBACK','42')
        self.assertEqual(reason,'QUEUED');self.assertEqual(closed.payload['position_ticket'],42)

    def test_fleet_highlights_only_verified_positions_from_fresh_telemetry(self):
        from .strategy_views import worker_rows
        command,_,_=open_for('TREND_PULLBACK')
        command.state='EXECUTED';command.result={'broker_ticket':'42'};command.save()
        self.feed.payload['positions']=[dict(ticket='42',symbol='XAUUSD_o',magic=STRATEGIES['TREND_PULLBACK'][2])]
        self.feed.save()
        rows={r['setup']:r for r in worker_rows()}
        self.assertEqual(rows['TREND_PULLBACK']['open_positions'],1)
        self.assertEqual(rows['BREAKOUT_RETEST']['open_positions'],0)
        self.assertTrue(rows['TREND_PULLBACK']['position_status_known'])
        # The open-position indicator remains meaningful when new entries are paused.
        StrategyWorkerControl.objects.filter(setup='TREND_PULLBACK').update(automatic=False)
        self.assertEqual(worker_rows()[0]['open_positions'],1)
        self.feed.payload['positions'][0]['magic']=0;self.feed.save()
        self.assertEqual(worker_rows()[0]['open_positions'],0)
        self.feed.payload['positions'][0]['magic']=STRATEGIES['TREND_PULLBACK'][2];self.feed.save()
        EAHeartbeat.objects.filter(pk=self.feed.pk).update(last_seen=timezone.now()-timedelta(seconds=61))
        row=worker_rows()[0]
        self.assertFalse(row['position_status_known']);self.assertEqual(row['open_positions'],0)

    def test_pause_and_missing_worker_do_not_affect_other_strategies(self):
        StrategyWorkerControl.objects.filter(setup='TREND_PULLBACK').update(automatic=False)
        self.assertEqual(open_for('TREND_PULLBACK')[2],'STRATEGY_PAUSED_OR_SCOPE_MISMATCH')
        self.assertIsNotNone(open_for('RANGE_EDGE_FADE')[0])

    def test_unknown_manual_positions_and_netting_fail_closed(self):
        self.feed.payload['positions']=[dict(ticket='manual',symbol='XAUUSD_o',magic=0)]
        self.feed.save()
        self.assertEqual(open_for('TREND_PULLBACK')[2],'STRATEGY_HEDGING_OWNERSHIP_REQUIRED')
        self.feed.payload.update(positions=[],account_margin_mode=0);self.feed.save()
        self.assertEqual(open_for('BREAKOUT_RETEST')[2],'STRATEGY_HEDGING_OWNERSHIP_REQUIRED')

    def test_account_risk_reservations_limit_other_workers(self):
        DecisionThresholdConfig.objects.create(profile='demo',values={'risk_management':{'max_total_risk_percent':.06}})
        self.assertIsNotNone(open_for('TREND_PULLBACK')[0])
        self.assertEqual(open_for('BREAKOUT_RETEST')[2],'PORTFOLIO_RISK_LIMIT_REACHED')

    def test_uncertain_command_only_blocks_its_own_strategy(self):
        command,_,_=open_for('TREND_PULLBACK');command.state='RECONCILIATION_REQUIRED';command.save()
        self.assertEqual(open_for('TREND_PULLBACK','next')[2],'STRATEGY_COMMAND_UNSETTLED')
        self.assertIsNotNone(open_for('BREAKOUT_RETEST')[0])

    def test_retired_public_endpoint_cannot_create_combined_decisions(self):
        response=self.client.post('/api/ea/v1/decision',{},content_type='application/json',HTTP_AUTHORIZATION='Bearer test-token')
        self.assertEqual(response.json()['reason'],'MONOLITH_RETIRED_USE_STRATEGY_CONTAINERS')
        self.assertFalse(DecisionEvaluation.objects.exists());self.assertFalse(EACommand.objects.exists())

    def test_controls_are_staff_csrf_protected_and_pause_is_scoped(self):
        a,_,_=open_for('TREND_PULLBACK');b,_,_=open_for('BREAKOUT_RETEST')
        url='/api/ea/v1/strategies/trend-pullback'
        self.assertEqual(self.client.get(url).status_code,302)
        user=get_user_model().objects.create_user(username='operator',password='local-test',is_staff=True)
        self.client.force_login(user)
        response=self.client.get(url);self.assertContains(response,'ramon-trend-pullback')
        from django.test import Client
        strict=Client(enforce_csrf_checks=True);strict.force_login(user)
        self.assertEqual(strict.post(url,{'action':'pause'}).status_code,403)
        control=StrategyWorkerControl.objects.get(setup='TREND_PULLBACK')
        self.client.post(url,{'action':'pause','scope':control.pk})
        a.refresh_from_db();b.refresh_from_db()
        self.assertEqual(a.state,'EXPIRED');self.assertEqual(b.state,'PENDING')
        self.assertTrue(StrategyWorkerControl.objects.get(setup='BREAKOUT_RETEST').automatic)

    def test_retired_learning_keeps_legacy_rows_and_excludes_setup_results(self):
        from .models import LearningTradeOutcome
        from .learning_store import DjangoOutcomeStore
        LearningTradeOutcome.objects.create(trade_id='legacy',realized_pnl=1,tags={})
        LearningTradeOutcome.objects.create(trade_id='setup',realized_pnl=2,tags={'strategy_profile':'SETUP_TREND_PULLBACK'})
        LearningTradeOutcome.objects.create(trade_id='scalp',realized_pnl=3,tags={'strategy_profile':'SCALPER_M1_M5'})
        self.assertEqual([r['trade_id'] for r in DjangoOutcomeStore().all()],['legacy'])

    def test_dashboard_links_all_seven_and_get_is_read_only(self):
        response=self.client.get('/api/ea/v1/dashboard')
        for slug,_,_ in STRATEGIES.values(): self.assertContains(response,'/api/ea/v1/strategies/'+slug)
        status=self.client.get('/api/ea/v1/strategies').json()
        self.assertEqual(len(status['workers']),7);self.assertTrue(status['monolith_retired'])
        self.assertFalse(EACommand.objects.exists())

    def test_worker_checks_feed_and_pause_before_evaluation(self):
        StrategyWorkerControl.objects.filter(setup='TREND_PULLBACK').update(automatic=False)
        with patch('ea_bridge.decision.evaluate_payload') as evaluate:
            self.assertEqual(worker_cycle('TREND_PULLBACK'),'PAUSED');evaluate.assert_not_called()
        self.feed.payload['account_trade_mode']=2;self.feed.save()
        self.assertEqual(worker_cycle('BREAKOUT_RETEST'),'DEMO_REQUIRED')


@override_settings(**SETTINGS)
@skipUnlessDBFeature('has_select_for_update')
class StrategyConcurrencyTests(TransactionTestCase):
    def test_all_seven_workers_can_queue_entries_concurrently_with_budget(self):
        fixture()
        setups=list(STRATEGIES)
        for setup in setups: approved(setup)
        barrier=Barrier(len(setups))
        def run(setup):
            try:
                decision=StrategyDecisionRecord.objects.get(decision_id=setup)
                verdict=RiskVerdictRecord.objects.get(decision_id=setup)
                barrier.wait(timeout=10)
                return create_open_command(decision=decision,verdict=verdict,
                    instance_id='demo',account_login='123',server_name='Demo')[1:]
            finally: connections.close_all()
        with ThreadPoolExecutor(max_workers=len(setups)) as pool:
            results=list(pool.map(run,setups))
        self.assertTrue(all(created for created,_ in results),results)
        commands=EACommand.objects.filter(action='OPEN')
        self.assertEqual(commands.count(),7)
        self.assertEqual(len({c.payload['magic'] for c in commands}),7)

    def test_two_workers_cannot_spend_same_account_headroom(self):
        fixture()
        DecisionThresholdConfig.objects.create(profile='demo',values={'risk_management':{'max_total_risk_percent':.06}})
        for setup in ['TREND_PULLBACK','BREAKOUT_RETEST']: approved(setup)
        barrier=Barrier(2)
        def run(setup):
            try:
                d=StrategyDecisionRecord.objects.get(decision_id=setup)
                v=RiskVerdictRecord.objects.get(decision_id=setup)
                barrier.wait(timeout=10)
                return create_open_command(decision=d,verdict=v,instance_id='demo',account_login='123',server_name='Demo')[1:]
            finally: connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(run,['TREND_PULLBACK','BREAKOUT_RETEST']))
        self.assertEqual(sum(created for created,_ in results),1,results)
        self.assertEqual(EACommand.objects.filter(action='OPEN').count(),1)
