from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timedelta, timezone as dt_timezone
import json

from django.test import TestCase, SimpleTestCase, override_settings
from django.utils import timezone

from ramon_pipeline.signalflow.scalping import (
    ScalpCosts, VERSION, detect_scalp, resolve_scalp, replay_scalper,
)
from .models import EACommand, EAHeartbeat, MarketBar, ScalpObservation
from .scalp_observer import candle, record_scalp, run_scalp_cycle, settle_scalps


def setup_bars(end=None):
    end = end or 1700006400
    end = int(end)//60*60
    m1 = []
    for i in range(64):
        close = 100+i*.1
        m1.append(dict(time=end-(63-i)*60, open=close-.05, high=close+.5, low=close-.5, close=close))
    m1[-1].update(open=106.2, close=107, high=107.1, low=106)
    last_m5 = (end+60-300)//300*300
    m5 = []
    for i in range(32):
        close = 90+i*.5
        m5.append(dict(time=last_m5-(31-i)*300, open=close-.2, high=close+.6, low=close-.6, close=close))
    return m1, m5


class ScalpRulesTests(SimpleTestCase):
    def setUp(self):
        self.m1, self.m5 = setup_bars()
        self.costs = ScalpCosts(.01, 1, 0, 0)

    def detect(self, m1=None, m5=None, costs=None):
        return detect_scalp(m1 or self.m1, m5 or self.m5, bid=107, costs=costs or self.costs, tick_size=.01)

    def test_causal_closed_m5_and_pullback_trigger(self):
        plan = self.detect()
        self.assertEqual(plan['action'], 'BUY', plan)
        future = dict(self.m5[-1], time=self.m1[-1]['time']+60, close=1)
        self.assertEqual(self.detect(m5=self.m5+[future]), plan)
        self.m1[-1]['close'] = 106.3
        self.assertEqual(self.detect()['action'], 'WAIT')

    def test_gap_and_high_costs_block_entries(self):
        self.assertEqual(self.detect(costs=ScalpCosts(.01, 100, 5, 5))['action'], 'WAIT')
        bad = deepcopy(self.m1); bad[-3]['time'] -= 1
        self.assertEqual(self.detect(m1=bad)['reason'], 'M1_GAP_OR_INVALID')
        bad5 = deepcopy(self.m5); bad5[-1]['time'] -= 300
        self.assertEqual(self.detect(m5=bad5)['reason'], 'M5_STALE_GAPPED_OR_INVALID')

    def test_explicit_cost_validation(self):
        for value in [-1, float('nan'), float('inf'), True]:
            with self.assertRaises(ValueError): ScalpCosts(.01, value, 0, 0)

    def test_costs_stop_first_and_adverse_gap(self):
        plan = dict(action='BUY', stop=90, target=112.5)
        costs = ScalpCosts(.01, 20, 5, 5)
        bar = dict(time=1700006400, open=100, low=89, high=113, close=101)
        result = resolve_scalp([bar], plan, costs)
        self.assertEqual(result['result'], 'STOP')
        self.assertTrue(result['ambiguous_stop_target'])
        self.assertAlmostEqual(result['entry'], 100.25)
        self.assertAlmostEqual(result['net_points'], -1035)
        bars = [dict(bar, low=99, high=101), dict(bar, time=bar['time']+60, open=85, low=84, high=86, close=85)]
        self.assertAlmostEqual(resolve_scalp(bars, plan, costs)['exit'], 84.95)

    def test_short_ask_side_exit_and_five_minute_timeout(self):
        plan = dict(action='SELL', stop=110, target=87.5)
        costs = ScalpCosts(.01, 20, 5, 5)
        bars = [dict(time=1700006400+i*60, open=100, low=99, high=109.9, close=100) for i in range(5)]
        self.assertEqual(resolve_scalp(bars, plan, costs)['result'], 'STOP')  # ask high 110.1
        for b in bars: b['high'] = 101
        outcome = resolve_scalp(bars, plan, costs)
        self.assertEqual(outcome['result'], 'TIME')
        self.assertEqual(outcome['bars_held'], 5)
        self.assertAlmostEqual(outcome['net_points'], -35)

    def test_next_open_cannot_force_invalid_geometry(self):
        b = dict(time=1700006400, open=113, high=114, low=112, close=113)
        self.assertEqual(resolve_scalp([b],dict(action='BUY',stop=90,target=112.5),self.costs)['result'], 'INVALID_AT_ENTRY')


@override_settings(RAMON_SCALP_SHADOW_ENABLED=True)
class ScalpObserverTests(TestCase):
    def setUp(self):
        # First 15 seconds after an M1 completion: issuance precedes next full minute.
        self.now = datetime.fromtimestamp(int(timezone.now().timestamp())//60*60+10, dt_timezone.utc)
        self.m1, self.m5 = setup_bars(int(self.now.timestamp())-60)
        for tf, rows in [('M1',self.m1),('M5',self.m5)]:
            for b in rows:
                row = MarketBar.objects.create(source='demo',symbol='GOLD',timeframe=tf,
                    timestamp=datetime.fromtimestamp(b['time'],dt_timezone.utc),
                    **{k:b[k] for k in ('open','high','low','close')})
                MarketBar.objects.filter(pk=row.pk).update(received_at=self.now-timedelta(seconds=1))
        self.feed = EAHeartbeat.objects.create(instance_id='demo',account_login='123',server_name='Demo',
            symbol='GOLD',status='TELEMETRY_OK',terminal_connected=True,
            payload=dict(account_trade_mode=0,server_name='Demo',quote_time=self.now.isoformat(),
                quote=dict(point=.01,bid=107,ask=107.01),symbol_info=dict(tick_size=.01,stops_level_points=0)))
        EAHeartbeat.objects.filter(pk=self.feed.pk).update(last_seen=self.now-timedelta(seconds=1))

    def test_records_once_per_signal_before_next_minute_and_no_orders(self):
        self.assertEqual(record_scalp(self.feed,now=self.now),1)
        self.assertEqual(record_scalp(self.feed,now=self.now),0)
        row = ScalpObservation.objects.get()
        self.assertEqual(row.action,'BUY',row.snapshot)
        self.assertLess(row.recorded_at,row.evaluation_start)
        self.assertEqual(row.snapshot['version'] if 'version' in row.snapshot else row.version,VERSION)
        self.assertFalse(EACommand.objects.exists())

    def test_missing_context_other_source_and_late_receipt_are_not_used(self):
        MarketBar.objects.filter(timeframe='M5').update(source='other')
        record_scalp(self.feed,now=self.now)
        self.assertEqual(ScalpObservation.objects.get().reason,'M5_WARMUP')
        ScalpObservation.objects.all().delete()
        MarketBar.objects.filter(timeframe='M5').update(source='demo',received_at=self.now+timedelta(seconds=1))
        record_scalp(self.feed,now=self.now)
        self.assertEqual(ScalpObservation.objects.get().reason,'M5_WARMUP')

    def test_stale_and_real_account_do_not_record(self):
        self.feed.payload['account_trade_mode'] = 2; self.feed.save()
        self.assertEqual(record_scalp(self.feed,now=self.now),0)
        self.feed.payload['account_trade_mode'] = 0
        self.feed.payload['quote_time'] = (self.now-timedelta(minutes=2)).isoformat();self.feed.save()
        EAHeartbeat.objects.filter(pk=self.feed.pk).update(last_seen=self.now)
        self.assertEqual(record_scalp(self.feed,now=self.now),0)

    def test_forward_settlement_freezes_first_observed_candle_and_never_rewrites(self):
        record_scalp(self.feed,now=self.now)
        row = ScalpObservation.objects.get()
        first = MarketBar.objects.create(source='demo',symbol='GOLD',timeframe='M1',timestamp=row.evaluation_start,
            open=107,high=107.2,low=106.9,close=107.1)
        MarketBar.objects.filter(pk=first.pk).update(received_at=row.evaluation_start+timedelta(minutes=1))
        settle_scalps(now=row.evaluation_start+timedelta(minutes=1,seconds=5))
        row.refresh_from_db()
        self.assertEqual(row.status,'WAITING')
        frozen = deepcopy(row.outcome['observed_bars'][0])
        MarketBar.objects.filter(pk=first.pk).update(low=90)
        for i in range(1,5):
            b = MarketBar.objects.create(source='demo',symbol='GOLD',timeframe='M1',timestamp=row.evaluation_start+timedelta(minutes=i),
                open=107,high=107.2,low=106.9,close=107.1)
            MarketBar.objects.filter(pk=b.pk).update(received_at=row.evaluation_end)
        settle_scalps(now=row.evaluation_end+timedelta(seconds=5))
        row.refresh_from_db()
        self.assertEqual(row.status,'SCORED')
        self.assertEqual(row.outcome['result'],'TIME')
        self.assertEqual(row.outcome['observed_bars'][0],frozen)
        self.assertEqual(settle_scalps(now=row.evaluation_end+timedelta(hours=1)),0)

    def test_missing_outcome_skips_without_filling_gaps(self):
        record_scalp(self.feed,now=self.now)
        row = ScalpObservation.objects.get()
        settle_scalps(now=row.evaluation_end+timedelta(minutes=11))
        row.refresh_from_db()
        self.assertEqual(row.status,'SKIPPED')
        self.assertNotIn('net_points',row.outcome)

    @override_settings(RAMON_SCALP_SHADOW_ENABLED=False)
    def test_disabled_cycle_and_get_never_write_observations_or_orders(self):
        self.assertEqual(run_scalp_cycle(),(0,0))
        response = self.client.get('/api/ea/v1/research/scalping')
        self.assertEqual(response.status_code,200)
        self.assertFalse(response.json()['execution_enabled'])
        self.assertEqual(ScalpObservation.objects.count(),0)
        self.assertEqual(EACommand.objects.count(),0)
