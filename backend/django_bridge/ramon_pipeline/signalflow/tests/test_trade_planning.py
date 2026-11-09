from copy import deepcopy
from datetime import datetime, timezone
from unittest import TestCase

from ramon_pipeline.signalflow.entry_timing import EntryTimer, EntryTimingConfig
from ramon_pipeline.signalflow.opportunities import OpportunityDetector
from ramon_pipeline.signalflow.pipeline import SignalFlowPipeline
from ramon_pipeline.signalflow.trade_planning import completed_context, market_context, plan_target, rank_candidate
from ramon_pipeline.signalflow.additional_setups import detect_additional_opportunity
from ramon_pipeline.signalflow.trading_config import load_trading_config

from .test_pipeline import market
from .test_risk_management import ACCOUNT


def context_bars(count, interval, cutoff, rising=True):
    result = []
    for i in range(count):
        close = 100 + i * (0.1 if rising else -0.1)
        result.append({"time": cutoff - (count-i)*interval, "open": close - (0.05 if rising else -0.05),
                       "high": close + 0.06, "low": close - 0.06, "close": close, "closed": True})
    return result


class ContextTests(TestCase):
    def snapshot(self):
        cutoff = 1_700_000_000
        return {"bars": [{"time": cutoff-900}], "decision_time": cutoff,
                "context_bars": {"H1": context_bars(60, 3600, cutoff), "M5": context_bars(24, 300, cutoff)}}

    def test_completed_trend_and_trigger_are_available(self):
        context = market_context(self.snapshot())
        self.assertEqual(context["H1"]["bias"], "BUY")
        self.assertEqual(context["M5"]["trigger"], "BUY")

    def test_incomplete_and_future_context_cannot_change_a_decision(self):
        snapshot = self.snapshot()
        before = market_context(snapshot)
        for timeframe, interval in (("H1", 3600), ("M5", 300)):
            extra = {"time": snapshot["decision_time"]-interval+1, "open": 1, "high": 99999, "low": 1, "close": 99999}
            snapshot["context_bars"][timeframe].append(extra)
        self.assertEqual(market_context(snapshot), before)

    def test_missing_stale_invalid_and_short_context_are_explicit(self):
        snapshot = self.snapshot()
        self.assertEqual(market_context({"bars": snapshot["bars"]})["H1"]["status"], "MISSING")
        snapshot["decision_time"] += 4*3600
        self.assertEqual(market_context(snapshot)["H1"]["status"], "STALE")
        snapshot = self.snapshot()
        snapshot["context_bars"]["H1"][-1]["close"] = float("nan")
        self.assertEqual(market_context(snapshot)["H1"]["status"], "INVALID")
        snapshot = self.snapshot()
        snapshot["context_bars"]["H1"] = snapshot["context_bars"]["H1"][-10:]
        self.assertEqual(market_context(snapshot)["H1"]["status"], "INSUFFICIENT_HISTORY")

    def test_context_gaps_are_not_interpreted_as_continuous_trends(self):
        snapshot = self.snapshot()
        snapshot["context_bars"]["H1"][-1]["time"] += 9*3600
        snapshot["decision_time"] += 9*3600
        self.assertEqual(completed_context(snapshot, "H1")[1], "GAPPED")

    def test_shifted_duplicate_context_is_not_treated_as_another_candle(self):
        snapshot = self.snapshot()
        duplicate = dict(snapshot["context_bars"]["M5"][-1])
        duplicate["time"] += 1
        snapshot["decision_time"] += 2
        snapshot["context_bars"]["M5"].append(duplicate)
        self.assertEqual(completed_context(snapshot, "M5")[1], "INVALID_CADENCE")

    def test_context_changes_rank_but_is_not_a_fake_probability(self):
        context = market_context(self.snapshot())
        timing, risk = {"quality": 0.8}, {"reward_risk": 1.5}
        buy = rank_candidate({"side": "BUY", "score": 0.7}, timing, risk, context)
        sell = rank_candidate({"side": "SELL", "score": 0.7}, timing, risk, context)
        self.assertGreater(buy["score"], sell["score"])
        self.assertEqual(buy["interpretation"], "HEURISTIC_RANK_NOT_WIN_PROBABILITY")


class TargetPlanningTests(TestCase):
    def plan(self, *, side="BUY", midpoint=None, bars=None, entry=100, stop=98):
        setup = {"type": "TREND_PULLBACK", "atr": 1.0}
        if midpoint is not None:
            setup.update(type="RANGE_EDGE_FADE", range_high=midpoint+5, range_low=midpoint-5)
        risk = {"approved": True, "entry_price": entry, "stop_price": stop,
                "target_reward_risk": 2.0, "volume": 0.1, "checks": []}
        return plan_target({"side": side, "setup": setup}, risk,
                           {"bars": bars or [], "point": 0.01, "bid": entry, "ask": entry}, 1.2)

    def test_range_midpoint_is_checked_before_selection(self):
        result = self.plan(midpoint=101)
        self.assertFalse(result["approved"])
        self.assertEqual(result["reason"], "TARGET_ROOM_TOO_SMALL")
        self.assertEqual(result["volume"], 0)
        self.assertEqual(self.plan(midpoint=103)["target_price"], 103)

    def test_buy_target_stays_in_front_of_confirmed_resistance(self):
        bars = [{"high": h, "low": 98, "close": 99} for h in (100, 101, 102, 103, 102, 101, 100)]
        result = self.plan(bars=bars)
        self.assertAlmostEqual(result["target_price"], 102.9)
        self.assertEqual(result["target_source"], "M15_SWING_BEFORE_OBSTACLE")
        self.assertTrue(result["approved"])

    def test_sell_targets_are_symmetric(self):
        bars = [{"low": lo, "high": 102, "close": 101} for lo in (100, 99, 98, 97, 98, 99, 100)]
        result = self.plan(side="SELL", stop=102, bars=bars)
        self.assertAlmostEqual(result["target_price"], 97.1)
        self.assertTrue(result["approved"])

    def test_broken_and_unconfirmed_levels_do_not_block_targets(self):
        broken = [{"high": h, "low": 98, "close": 99} for h in (100, 101, 102, 103, 102, 101, 105)]
        broken[-1]["close"] = 104
        self.assertEqual(self.plan(bars=broken)["target_price"], 104)
        self.assertEqual(self.plan(bars=broken[:5])["target_source"], "CONFIGURED_R_MULTIPLE")


class CandidateSelectionTests(TestCase):
    def test_real_detector_previews_all_seven_setups_without_consuming_them(self):
        detector = OpportunityDetector(demo_mode=True)
        candidates = detector.evaluate_candidates(market())
        self.assertEqual(len(candidates), 7)
        self.assertTrue(any(item["approved"] for item in candidates))
        self.assertEqual(detector._accepted, {})
        self.assertEqual([item["setup_name"] for item in candidates],
                         [item["setup_name"] for item in detector.evaluate_candidates(market())])

    def test_shifted_m15_duplicates_are_rejected_before_indicator_computation(self):
        snapshot = market()
        snapshot["bars"].insert(-1, {**snapshot["bars"][-1], "time": snapshot["bars"][-1]["time"]-1})
        result = OpportunityDetector().evaluate(snapshot)
        self.assertEqual(result["reason"], "INVALID_CANDLE_CADENCE")
        self.assertFalse(result["approved"])

    def snapshot(self):
        stamp = 1_700_000_000
        return {**ACCOUNT, "bars": [{"time": stamp, "open": 100, "high": 100.2, "low": 99.8, "close": 100}],
                "bid": 100, "ask": 100.1, "point": 0.01, "latest_bar_time": stamp,
                "target_reward_risk": 1.5, "entry_quality": 0.01}

    def candidate(self, name, score, invalidation=98):
        return {"approved": True, "status": "ACCEPTED", "side": "BUY", "score": score,
                "reason": "CONFIRMED", "signal_bar_time": 1_700_000_000, "dedupe_key": name,
                "setup": {"type": name, "atr": 2, "reference_price": 100, "invalidation_price": invalidation}}

    def pipeline(self, candidates):
        class Detector:
            def __init__(self):
                self.selected = []

            def evaluate_candidates(self, snapshot):
                return deepcopy(candidates)

            def remember(self, candidate):
                self.selected.append(candidate["dedupe_key"])

        return SignalFlowPipeline(opportunities=Detector(), compare_candidates=True,
                                  entry_timer=EntryTimer(config=EntryTimingConfig(ignore_supplied_quality=True)))

    def test_unexecutable_first_candidate_cannot_hide_a_valid_alternative(self):
        pipeline = self.pipeline([self.candidate("INVALID", 0.99, invalidation=101), self.candidate("VALID", 0.7)])
        result = pipeline.run(self.snapshot())
        self.assertTrue(result["approved"], result["reason"])
        self.assertEqual(result["selection"]["selected_setup"], "VALID")
        self.assertEqual(pipeline.opportunities.selected, ["VALID"])
        self.assertEqual(result["stages"]["entry_timing"]["quality_source"], "COMPUTED")

    def test_ranking_is_independent_of_detector_iteration_order(self):
        candidates = [self.candidate("LOW", 0.6), self.candidate("HIGH", 0.9)]
        for ordered in (candidates, candidates[::-1]):
            result = self.pipeline(ordered).run(self.snapshot())
            self.assertEqual(result["selection"]["selected_setup"], "HIGH")
            self.assertEqual(result["selection"]["executable_candidates"], 2)

    def test_range_with_unreachable_midpoint_does_not_hide_a_valid_trend_trade(self):
        range_candidate = self.candidate("RANGE_EDGE_FADE", 0.99)
        range_candidate["setup"].update(range_high=106, range_low=96)
        result = self.pipeline([range_candidate, self.candidate("TREND", 0.7)]).run(self.snapshot())
        self.assertEqual(result["selection"]["selected_setup"], "TREND")
        rejected = next(c for c in result["selection"]["candidates"] if c["setup"] == "RANGE_EDGE_FADE")
        self.assertEqual(rejected["risk_reason"], "TARGET_ROOM_TOO_SMALL")

    def test_no_valid_candidate_means_hold_without_consuming_a_signal(self):
        pipeline = self.pipeline([self.candidate("INVALID", 0.99, invalidation=101)])
        result = pipeline.run(self.snapshot())
        self.assertFalse(result["approved"])
        self.assertEqual(result["action"], "HOLD")
        self.assertEqual(pipeline.opportunities.selected, [])


class SupplementalTriggerTests(TestCase):
    def detect(self, times, opens, highs, lows, closes, enabled):
        setups = load_trading_config()["opportunity_finder"]["setups"]
        for name, settings in setups.items():
            settings["enabled"] = name == enabled
        setups["session_opening_range_breakout"]["sessions"] = {"TEST": {"timezone": "UTC", "open": "08:00"}}
        count = len(times)
        return detect_additional_opportunity(times, opens, highs, lows, closes, [2.0]*count,
                                             [15.0]*count, [50.0]*count, [], [], "BULLISH", setups)

    def test_opening_breakout_requires_a_complete_range_and_a_new_cross(self):
        start = datetime(2026, 11, 2, 8, tzinfo=timezone.utc).timestamp()
        args = ([start, start+900, start+1800], [100, 100, 100], [101, 101, 103], [99, 99, 99], [100, 100, 102])
        self.assertIsNotNone(self.detect(*args, "session_opening_range_breakout"))
        continued = tuple(list(value)+[value[-1]] for value in args)
        continued[0][-1] += 900
        self.assertIsNone(self.detect(*continued, "session_opening_range_breakout"))
        args[0][0] -= 900  # only one of the two opening-range candles remains
        self.assertIsNone(self.detect(*args, "session_opening_range_breakout"))

    def test_compression_requires_a_close_outside_a_genuinely_compact_range(self):
        times = [1_700_000_000+i*900 for i in range(23)]
        opens, highs, lows, closes = [101]*23, [103]*23, [100]*23, [101]*23
        opens[-1], highs[-1], closes[-1] = 101, 105, 104
        self.assertIsNotNone(self.detect(times, opens, highs, lows, closes, "volatility_compression_expansion"))
        closes[-1], opens[-1] = 102.5, 100
        self.assertIsNone(self.detect(times, opens, highs, lows, closes, "volatility_compression_expansion"))
        closes[-1], opens[-1] = 104, 101
        highs[5] = 120
        self.assertIsNone(self.detect(times, opens, highs, lows, closes, "volatility_compression_expansion"))
