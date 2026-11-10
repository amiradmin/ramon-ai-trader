from datetime import timedelta

from django.test import TestCase
from . import tests_candidate_observer as fixtures
NOW=fixtures.NOW
from .models import CandidateObservation, MarketBar, EACommand
from .plan_observer import evaluate_plan, settle_plans


class PlanObserverTests(TestCase):
    record=fixtures.CandidateObserverTests.record
    candles=fixtures.CandidateObserverTests.candles
    def setUp(self):
        fixtures.CandidateObserverTests.setUp(self)
        items=self.decision.features["selection"]["candidates"]
        items[0].update(stop_price=98,target_price=104)
        items[1].update(stop_price=104,target_price=98)

    def test_plan_is_frozen_and_missing_levels_do_not_get_invented(self):
        self.record()
        self.assertEqual(CandidateObservation.objects.get(side="BUY").plan_status,"WAITING")
        self.assertEqual(CandidateObservation.objects.get(side="HOLD").plan_status,"UNAVAILABLE")
        self.decision.features["selection"]["candidates"][0]["stop_price"]=50
        self.record()
        self.assertEqual(CandidateObservation.objects.get(side="BUY").plan_outcome["stop"],98)

    def test_stop_first_and_short_ask_exits_and_adverse_gap(self):
        bar={"time":"test","open":100,"high":105,"low":97,"close":100}
        buy=evaluate_plan([bar],side="BUY",stop=98,target=104,spread=.2,point=.01)
        self.assertEqual(buy["result"],"STOP")
        self.assertTrue(buy["ambiguous_stop_target"])
        sell=evaluate_plan([{**bar,"high":103.9,"low":99}],side="SELL",stop=104,target=98,spread=.2,point=.01)
        self.assertEqual(sell["result"],"STOP")
        gap=evaluate_plan([{**bar,"high":101,"low":99},
            {**bar,"open":96}],side="BUY",stop=98,target=104,spread=.2,point=.01)
        self.assertEqual(gap["result"],"STOP_GAP")
        self.assertLess(gap["r"],-1)

    def test_invalid_open_and_target_gap_have_no_fake_favorable_fill(self):
        bar={"time":"test","open":105,"high":106,"low":105,"close":106}
        self.assertEqual(evaluate_plan([bar],side="BUY",stop=98,target=104,spread=.2,point=.01)["result"],"INVALID_AT_ENTRY")
        first={"time":"test","open":100,"high":101,"low":99,"close":100}
        result=evaluate_plan([first,bar],side="BUY",stop=98,target=104,spread=.2,point=.01)
        self.assertEqual(result["exit"],104)

    def test_time_exit_is_exactly_24_bars(self):
        bar={"time":"test","open":100,"high":101,"low":99,"close":100.5}
        kwargs=dict(side="BUY",stop=98,target=104,spread=.2,point=.01)
        self.assertEqual(evaluate_plan([bar]*23,**kwargs)["result"],"OPEN")
        self.assertEqual(evaluate_plan([bar]*24,**kwargs)["result"],"TIME_EXIT")

    def test_worker_uses_completed_received_bars_and_terminal_outcomes_are_immutable(self):
        self.record();self.candles()
        self.assertEqual(settle_plans(now=NOW+timedelta(minutes=20)),0)
        self.assertEqual(settle_plans(now=NOW+timedelta(minutes=76)),2)
        buy=CandidateObservation.objects.get(side="BUY")
        self.assertEqual(buy.plan_outcome["result"],"TARGET")
        MarketBar.objects.update(close=999)
        self.assertEqual(settle_plans(now=NOW+timedelta(hours=7)),0)
        buy.refresh_from_db()
        self.assertEqual(buy.plan_outcome["result"],"TARGET")
        self.assertFalse(EACommand.objects.exists())

    def test_processed_prefix_is_frozen_and_missing_candles_are_not_skipped_over(self):
        self.record();self.candles(missing=1)
        MarketBar.objects.update(high=101,low=99,close=100)
        settle_plans(now=NOW+timedelta(minutes=76))
        buy=CandidateObservation.objects.get(side="BUY")
        self.assertEqual(buy.plan_status,"OPEN")
        self.assertEqual(len(buy.plan_outcome["observed_bars"]),1)
        MarketBar.objects.filter(timestamp=buy.evaluation_start).update(high=999)
        settle_plans(now=NOW+timedelta(hours=4))
        buy.refresh_from_db()
        self.assertEqual(buy.plan_status,"SKIPPED")
        self.assertEqual(buy.plan_outcome["result"],"DATA_GAP")
