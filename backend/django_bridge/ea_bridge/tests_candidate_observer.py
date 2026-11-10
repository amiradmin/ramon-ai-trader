from datetime import datetime, timedelta, timezone as tz
from copy import deepcopy
from unittest.mock import patch

from django.test import TestCase

from .candidate_observer import record_candidates, settle_candidates
from .models import CandidateObservation, EACommand, EAHeartbeat, MarketBar, StrategyDecisionRecord

NOW = datetime(2026, 11, 9, 9, 0, 5, tzinfo=tz.utc)


class CandidateObserverTests(TestCase):
    def setUp(self):
        self.decision = StrategyDecisionRecord.objects.create(decision_id="candidate-test", symbol="XAUUSD_o",
            action="BUY", confidence=.7, rationale_code="APPROVED", model_version="test",
            features={"selection":{"selected_setup":"TREND_PULLBACK", "context":{}, "candidates":[
                {"setup":"TREND_PULLBACK","side":"BUY","approved":True,"entry_reason":"ENTRY_CONFIRMED"},
                {"setup":"BREAKOUT_RETEST","side":"SELL","approved":False,"entry_reason":"CHASE_TOO_FAR"},
                {"setup":"RANGE_EDGE_FADE","side":"HOLD","approved":False,"opportunity_reason":"NO_SETUP"}]}})
        self.market = {"source":"demo","bar_time":(NOW-timedelta(minutes=15,seconds=5)).timestamp(),
                       "bid":100,"ask":100.2,"point":.01}
        EAHeartbeat.objects.create(instance_id="demo",symbol="XAUUSD_o",payload={"account_trade_mode":0})

    def record(self):
        record_candidates(self.decision,self.market,now=NOW)

    def candles(self, source="demo", missing=None, offset=timedelta(0)):
        start=NOW.replace(minute=15,second=0)+offset
        for i in range(4):
            if i == missing:
                continue
            bar=MarketBar.objects.create(source=source,symbol="XAUUSD_o",timeframe="M15",timestamp=start+timedelta(minutes=15*i),
                open=100+i,high=102+i,low=99+i,close=101+i)
            MarketBar.objects.filter(pk=bar.pk).update(received_at=NOW+timedelta(minutes=76))

    def test_snapshots_are_forward_immutable_idempotent_and_never_create_commands(self):
        self.record()
        self.decision.features["selection"]["candidates"][0]["entry_reason"]="CHANGED"
        self.record()
        self.assertEqual(CandidateObservation.objects.count(),3)
        row=CandidateObservation.objects.get(setup="TREND_PULLBACK")
        self.assertEqual(row.snapshot["candidate"]["entry_reason"],"ENTRY_CONFIRMED")
        self.assertEqual(row.evaluation_start,NOW.replace(minute=15,second=0))
        self.assertLess(row.recorded_at,row.evaluation_start)
        self.assertEqual(row.disposition,"SELECTED")
        self.assertEqual(CandidateObservation.objects.get(side="HOLD").status,"SKIPPED")
        self.assertFalse(EACommand.objects.exists())

    def test_buy_and_rejected_sell_are_scored_on_exact_future_window_once(self):
        self.record(); self.candles()
        self.assertEqual(settle_candidates(now=NOW+timedelta(minutes=74)),0)
        self.assertEqual(settle_candidates(now=NOW+timedelta(minutes=76)),2)
        buy=CandidateObservation.objects.get(side="BUY")
        sell=CandidateObservation.objects.get(side="SELL")
        self.assertAlmostEqual(buy.outcome["spread_adjusted_points"],380)
        self.assertAlmostEqual(sell.outcome["spread_adjusted_points"],-420)
        self.assertEqual(sell.disposition,"REJECTED")
        MarketBar.objects.update(close=999)
        self.assertEqual(settle_candidates(now=NOW+timedelta(hours=2)),0)
        buy.refresh_from_db()
        self.assertAlmostEqual(buy.outcome["spread_adjusted_points"],380)
        self.assertFalse(EACommand.objects.exists())

    def test_other_source_missing_and_off_grid_candles_are_never_substituted(self):
        self.record(); self.candles(source="other"); self.candles(missing=2)
        self.assertEqual(settle_candidates(now=NOW+timedelta(minutes=76)),0)
        self.assertEqual(settle_candidates(now=NOW+timedelta(hours=4)),2)
        self.assertFalse(CandidateObservation.objects.filter(status="SCORED").exists())

    def test_off_grid_outcome_rows_and_late_receipts_are_rejected(self):
        self.record(); self.candles(offset=timedelta(seconds=1))
        settle_candidates(now=NOW+timedelta(hours=4))
        self.assertFalse(CandidateObservation.objects.filter(status="SCORED").exists())

    def test_late_decisions_do_not_become_forward_results(self):
        record_candidates(self.decision,self.market,now=NOW+timedelta(minutes=5))
        self.assertFalse(CandidateObservation.objects.filter(status="WAITING").exists())

    def test_outcomes_received_later_are_not_visible_to_an_earlier_settlement(self):
        self.record(); self.candles()
        MarketBar.objects.update(received_at=NOW+timedelta(minutes=78))
        self.assertEqual(settle_candidates(now=NOW+timedelta(minutes=76)),0)
        self.assertEqual(settle_candidates(now=NOW+timedelta(minutes=79)),2)

    def test_get_does_not_settle_or_create_observations(self):
        self.record(); self.candles()
        with patch("ea_bridge.candidate_observer.timezone.now",return_value=NOW+timedelta(hours=2)):
            data=self.client.get("/api/ea/v1/research/candidates").json()
        self.assertEqual(data["counts"]["WAITING"],2)
        self.assertEqual(len(data["groups"]),3)
        self.assertFalse(data["affects_orders"])
        self.assertEqual(CandidateObservation.objects.filter(status="SCORED").count(),0)
