from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timedelta, timezone as utc

from django.db import DatabaseError, transaction
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from ramon_pipeline.signalflow.trend_shadow import (
    VERSION, TrendCosts, aggregate_h4, detect_trend, overnight_charge, resolve_trend, replay_trend,
)
from .models import EACommand, EAHeartbeat, MarketBar, TrendObservation
from .trend_observer import record_trend, settle_trends, run_trend_cycle


def history(end):
    end=int(end)//14400*14400+10800
    bars=[]
    for i in range(320):
        close=100+i*.2
        bars.append(dict(time=end-(319-i)*3600,open=close-.1,high=close+.3,low=close-.3,close=close))
    bars[-1].update(close=165,high=165.2)
    return bars


class TrendRulesTests(SimpleTestCase):
    def setUp(self):
        self.bars=history(1762732800)
        self.costs=TrendCosts(.01,1,0,0,0)

    def test_closed_h4_and_directional_breakout(self):
        plan=detect_trend(self.bars,bid=165,costs=self.costs,tick_size=.01)
        self.assertEqual(plan['action'],'BUY',plan)
        h4=aggregate_h4(self.bars)
        self.assertEqual(len(h4),80)
        self.assertLessEqual(h4[-1]['time']+14400,self.bars[-1]['time']+3600)
        down=[dict(time=b['time'],open=300-b['open'],high=300-b['low'],low=300-b['high'],close=300-b['close']) for b in self.bars]
        self.assertEqual(detect_trend(down,bid=135,costs=self.costs,tick_size=.01)['action'],'SELL')

    def test_incomplete_h4_not_filled_and_recent_gap_blocks(self):
        self.assertEqual(len(aggregate_h4(self.bars[:-1])),79)
        bad=deepcopy(self.bars); del bad[-7]
        self.assertEqual(detect_trend(bad,bid=165,costs=self.costs,tick_size=.01)['reason'],'RECENT_H1_GAP')
        bad=deepcopy(self.bars);bad[-1]['time']+=1
        self.assertEqual(detect_trend(bad,bid=165,costs=self.costs,tick_size=.01)['reason'],'INVALID_OR_UNSORTED_H1')

    def test_costs_include_overnight_and_validate(self):
        expensive=TrendCosts(.01,1,0,0,500)
        self.assertEqual(detect_trend(self.bars,bid=165,costs=expensive,tick_size=.01)['action'],'WAIT')
        for v in [-1,float('inf'),float('nan'),True]:
            with self.assertRaises(ValueError): TrendCosts(.01,1,0,0,v)
        wed=datetime(2026,11,11,21,tzinfo=utc.utc).timestamp()
        self.assertEqual(overnight_charge(wed,wed+7200,TrendCosts(.01,1,0,0,50)),150)

    def test_stop_first_gap_and_ask_short(self):
        stamp=self.bars[-1]['time']+3600
        bar=dict(time=stamp,open=100,high=131,low=89,close=100)
        costs=TrendCosts(.01,20,5,5,0)
        result=resolve_trend([bar],dict(action='BUY',stop=90,target=130),costs)
        self.assertEqual(result['result'],'STOP')
        self.assertTrue(result['ambiguous_stop_target'])
        self.assertAlmostEqual(result['net_points'],-1035)
        short=dict(bar,high=109.9,low=99)
        self.assertEqual(resolve_trend([short],dict(action='SELL',stop=110,target=70),costs)['result'],'STOP')
        first=dict(bar,high=101,low=99)
        second=dict(first,time=stamp+3600,open=85,low=84,high=86,close=85)
        self.assertAlmostEqual(resolve_trend([first,second],dict(action='BUY',stop=90,target=130),costs)['exit'],84.95)

    def test_timeout_funding_and_entry_geometry(self):
        stamp=datetime(2026,11,10,21,tzinfo=utc.utc).timestamp()
        bar=dict(time=stamp,open=100,high=101,low=99,close=100)
        costs=TrendCosts(.01,20,5,5,1)
        result=resolve_trend([bar],dict(action='BUY',stop=90,target=130),costs,max_holding_bars=1)
        self.assertEqual(result['result'],'TIME')
        self.assertAlmostEqual(result['net_points'],-36)
        self.assertEqual(resolve_trend([dict(bar,open=135,high=136,low=134,close=135)],
            dict(action='BUY',stop=90,target=130),costs)['result'],'INVALID_AT_ENTRY')

    def test_replay_does_not_mutate_inputs_and_has_zero_baseline(self):
        before=deepcopy(self.bars)
        result=replay_trend(self.bars,costs=self.costs,tick_size=.01)
        self.assertEqual(self.bars,before)
        self.assertEqual(result['no_trade_baseline_net_points'],0)
        self.assertIn('closed_cumulative_r_drawdown',result)


@override_settings(RAMON_TREND_SHADOW_ENABLED=True,RAMON_TREND_INSTANCE='demo',
                   RAMON_TREND_LOGIN='123',RAMON_TREND_SERVER='Demo')
class TrendObserverTests(TestCase):
    def setUp(self):
        now=timezone.now()
        end=int(now.timestamp())//14400*14400-3600
        self.now=datetime.fromtimestamp(end+3610,utc.utc)
        self.bars=history(end-10800)
        # Ensure fixture closes exactly at self.now's hour boundary.
        shift=end-self.bars[-1]['time']
        for b in self.bars: b['time']+=shift
        for b in self.bars:
            row=MarketBar.objects.create(source='demo',symbol='GOLD',timeframe='H1',
                timestamp=datetime.fromtimestamp(b['time'],utc.utc),**{k:b[k] for k in ['open','high','low','close']})
            MarketBar.objects.filter(pk=row.pk).update(received_at=self.now-timedelta(seconds=1))
        self.feed=EAHeartbeat.objects.create(instance_id='demo',account_login='123',server_name='Demo',
            symbol='GOLD',status='TELEMETRY_OK',terminal_connected=True,
            payload=dict(account_trade_mode=0,server_name='Demo',quote_time=self.now.isoformat(),
                quote=dict(point=.01,bid=165,ask=165.01),symbol_info=dict(tick_size=.01)))
        EAHeartbeat.objects.filter(pk=self.feed.pk).update(last_seen=self.now)

    @override_settings(RAMON_TREND_OVERNIGHT_POINTS=0)
    def test_forward_record_dedup_receipts_and_no_orders(self):
        self.assertEqual(record_trend(self.feed,now=self.now),1)
        self.assertEqual(record_trend(self.feed,now=self.now),0)
        row=TrendObservation.objects.get()
        self.assertEqual(row.action,'BUY',row.snapshot['plan'])
        self.assertLess(row.recorded_at,row.evaluation_start)
        self.assertEqual(row.snapshot['inputs'][-1]['time']+3600,int(self.now.timestamp())-10)
        self.assertFalse(EACommand.objects.exists())
        response=self.client.get('/api/ea/v1/research/trend')
        self.assertEqual(response.status_code,200)
        self.assertFalse(response.json()['execution_enabled'])
        self.assertEqual(TrendObservation.objects.count(),1)

    def test_future_and_other_source_bars_do_not_enter_snapshot(self):
        MarketBar.objects.filter(source='demo').update(received_at=self.now+timedelta(seconds=1))
        self.assertEqual(record_trend(self.feed,now=self.now),0)
        MarketBar.objects.update(received_at=self.now-timedelta(seconds=1),source='other')
        self.assertEqual(record_trend(self.feed,now=self.now),0)

    def test_real_or_stale_feed_blocks(self):
        self.feed.payload['account_trade_mode']=2;self.feed.save()
        self.assertEqual(record_trend(self.feed,now=self.now),0)
        self.assertFalse(TrendObservation.objects.exists())

    @override_settings(RAMON_TREND_OVERNIGHT_POINTS=0)
    def test_frozen_outcomes_and_terminal_immutability(self):
        record_trend(self.feed,now=self.now); row=TrendObservation.objects.get()
        self.assertEqual(row.action,'BUY')
        bar=MarketBar.objects.create(source='demo',symbol='GOLD',timeframe='H1',timestamp=row.evaluation_start,
            open=165,high=165.1,low=164.9,close=165)
        MarketBar.objects.filter(pk=bar.pk).update(received_at=row.evaluation_start+timedelta(hours=1))
        settle_trends(now=row.evaluation_start+timedelta(hours=1,seconds=10))
        row.refresh_from_db(); frozen=deepcopy(row.outcome['observed_bars'][0])
        MarketBar.objects.filter(pk=bar.pk).update(low=1)
        settle_trends(now=row.evaluation_start+timedelta(hours=1,minutes=10))
        row.refresh_from_db();self.assertEqual(row.outcome['observed_bars'][0],frozen)
        settle_trends(now=row.evaluation_start+timedelta(hours=4))
        row.refresh_from_db();self.assertEqual(row.status,'SKIPPED')
        self.assertNotIn('net_points',row.outcome)
        with self.assertRaises(DatabaseError),transaction.atomic():
            TrendObservation.objects.filter(pk=row.pk).update(action='SELL')
        with self.assertRaises(DatabaseError),transaction.atomic():
            TrendObservation.objects.filter(pk=row.pk).update(outcome={'result':'TARGET'})

    @override_settings(RAMON_TREND_SHADOW_ENABLED=False)
    def test_disabled_worker_and_get_never_write(self):
        self.assertEqual(run_trend_cycle(),(0,0))
        self.client.get('/api/ea/v1/research/trend')
        self.assertFalse(TrendObservation.objects.exists())
        self.assertFalse(EACommand.objects.exists())
