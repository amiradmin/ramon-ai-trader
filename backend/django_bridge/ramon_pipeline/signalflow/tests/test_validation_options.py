"""Tests for the opt-in validation/safety options added after the validation review.

Every option defaults to the original behaviour; these tests prove both that the default is
unchanged and that the option does what it says.
"""
import threading
from collections import OrderedDict
from unittest import TestCase

from ramon_pipeline.signalflow.entry_timing import EntryTimer, EntryTimingConfig
from ramon_pipeline.signalflow.opportunities import OpportunityConfig, OpportunityDetector
from ramon_pipeline.signalflow.pipeline import SignalFlowPipeline
from ramon_pipeline.signalflow.risk_management import RiskConfig, RiskManager

from .test_entry_timing import BAR, T0, opportunity as timing_opportunity, quote
from .test_opportunities import LONG_SETUP, SHORT_SETUP, UPTREND, make_bars, snapshot
from .test_risk_management import ACCOUNT, opportunity as risk_opportunity, structural_market

# Price patterns found by search: they differ only in how the impulse start is chosen.
MINOR_LOW_HIDES_LEG = UPTREND + [(12, 10), (3, -1.0), (2, 2.5), (5, -2.0), (2, 1.2)]
WHOLE_LEG_BROKEN = UPTREND + [(10, 8), (3, -0.8), (4, 6), (6, -5), (2, 2.5)]


class ClosedBarFlagTests(TestCase):
    bars = make_bars(LONG_SETUP)

    def test_default_still_assumes_a_missing_flag_means_closed(self):
        self.assertTrue(OpportunityDetector().evaluate(snapshot(self.bars))["approved"])

    def test_require_closed_flag_defers_unless_the_flag_is_exactly_true(self):
        detector = OpportunityDetector(config=OpportunityConfig(require_closed_flag=True))
        for market in (snapshot(self.bars), snapshot(self.bars, last_bar_closed=None),
                       snapshot(self.bars, last_bar_closed=False)):
            result = detector.evaluate(market)
            self.assertEqual((result["status"], result["reason"]), ("DEFERRED", "LAST_BAR_NOT_CLOSED"))
            self.assertFalse(result["approved"])
        self.assertTrue(detector.evaluate(snapshot(self.bars, last_bar_closed=True))["approved"])

    def test_unsupported_symbol_message_uses_the_configured_prefixes(self):
        result = OpportunityDetector().evaluate({**snapshot(self.bars), "symbol": "EURUSD"})
        self.assertEqual(result["reason"], "UNSUPPORTED_SYMBOL")
        self.assertIn("XAUUSD", result["explanation"])


class ImpulseStartModeTests(TestCase):
    lowest = OpportunityConfig(impulse_start_mode="LOWEST_SINCE_PRIOR_HIGH")

    def run_modes(self, legs):
        bars = make_bars(legs)
        return (OpportunityDetector().evaluate(snapshot(bars)),
                OpportunityDetector(config=self.lowest).evaluate(snapshot(bars)))

    def test_invalid_mode_is_rejected(self):
        with self.assertRaises(ValueError):
            OpportunityConfig(impulse_start_mode="NEWEST")

    def test_clean_setups_are_unchanged_in_both_directions(self):
        for legs, side in ((LONG_SETUP, "BUY"), (SHORT_SETUP, "SELL")):
            kwargs = {"start_price": 2400.0 if side == "BUY" else 2600.0}
            bars = make_bars(legs, **kwargs)
            default = OpportunityDetector().evaluate(snapshot(bars))
            lowest = OpportunityDetector(config=self.lowest).evaluate(snapshot(bars))
            self.assertEqual((default["reason"], default["side"]), (lowest["reason"], lowest["side"]))
            self.assertEqual(lowest["side"], side)

    def test_a_minor_fractal_low_no_longer_hides_the_real_leg(self):
        default, lowest = self.run_modes(MINOR_LOW_HIDES_LEG)
        self.assertEqual(default["reason"], "PULLBACK_TOO_DEEP")
        self.assertTrue(lowest["approved"])

    def test_the_default_can_also_accept_a_pullback_that_broke_the_whole_leg(self):
        # The change is not one-directional: measuring the full leg also removes false accepts.
        default, lowest = self.run_modes(WHOLE_LEG_BROKEN)
        self.assertTrue(default["approved"])
        self.assertEqual(lowest["reason"], "PULLBACK_TOO_DEEP")


class SignalReleaseAndSharedStoreTests(TestCase):
    bars = make_bars(LONG_SETUP + [(1, 1.0)])

    def test_accepted_signals_expose_their_dedupe_key(self):
        result = OpportunityDetector().evaluate(snapshot(self.bars[:-1]))
        self.assertTrue(result["approved"])
        self.assertTrue(result["dedupe_key"].startswith("XAUUSD_o|M15|BUY|"))

    def test_released_signal_can_fire_again_on_a_later_bar(self):
        detector = OpportunityDetector()
        first = detector.evaluate(snapshot(self.bars[:-1]))
        self.assertEqual(detector.evaluate(snapshot(self.bars))["reason"], "DUPLICATE_SIGNAL")
        self.assertTrue(detector.release(first["dedupe_key"], first["signal_bar_time"]))
        self.assertTrue(detector.evaluate(snapshot(self.bars))["approved"])

    def test_release_ignores_unknown_keys_and_other_bars(self):
        detector = OpportunityDetector()
        first = detector.evaluate(snapshot(self.bars[:-1]))
        self.assertFalse(detector.release("missing"))
        self.assertFalse(detector.release(first["dedupe_key"], first["signal_bar_time"] + BAR))

    def test_separate_detectors_do_not_share_memory_unless_a_store_is_injected(self):
        self.assertTrue(OpportunityDetector().evaluate(snapshot(self.bars[:-1]))["approved"])
        # a fresh detector (what a per-request pipeline gets) has forgotten the signal
        self.assertTrue(OpportunityDetector().evaluate(snapshot(self.bars))["approved"])

        store, lock = OrderedDict(), threading.Lock()
        OpportunityDetector(accepted_store=store, accepted_lock=lock).evaluate(snapshot(self.bars[:-1]))
        later = OpportunityDetector(accepted_store=store, accepted_lock=lock).evaluate(snapshot(self.bars))
        self.assertEqual(later["reason"], "DUPLICATE_SIGNAL")


class PipelineDeferredReleaseTests(TestCase):
    def market(self, bars, spread_points):
        close = bars[-1]["close"]
        return snapshot(bars, bid=close, ask=close + spread_points * 0.01, point=0.01, **ACCOUNT)

    def test_a_deferred_entry_does_not_burn_the_signal(self):
        bars = make_bars(LONG_SETUP + [(1, 1.0)])
        pipeline = SignalFlowPipeline(
            opportunities=OpportunityDetector(), entry_timer=EntryTimer(require_quote=True))
        wide = pipeline.run(self.market(bars[:-1], spread_points=120))
        self.assertEqual((wide["rejected_by"], wide["reason"]), ("entry_timing", "SPREAD_OUT_OF_RANGE"))
        retry = pipeline.run(self.market(bars, spread_points=20))
        self.assertNotEqual(retry["stages"]["opportunities"]["reason"], "DUPLICATE_SIGNAL")
        self.assertTrue(retry["stages"]["opportunities"]["approved"])

    def test_a_hard_rejection_downstream_still_consumes_the_signal(self):
        bars = make_bars(LONG_SETUP + [(1, 1.0)])
        pipeline = SignalFlowPipeline(
            opportunities=OpportunityDetector(), entry_timer=EntryTimer(require_quote=True),
            risk_manager=RiskManager())
        no_balance = {**self.market(bars[:-1], spread_points=20), "account_balance": 1.0}
        first = pipeline.run(no_balance)
        self.assertEqual(first["rejected_by"], "risk_management")
        self.assertEqual(first["stages"]["risk_management"]["status"], "REJECTED")
        again = pipeline.run(self.market(bars, spread_points=20))
        self.assertEqual(again["stages"]["opportunities"]["reason"], "DUPLICATE_SIGNAL")


class EntryTimingOptionTests(TestCase):
    def test_missing_signal_time_skips_freshness_by_default_but_can_fail_closed(self):
        opp = {k: v for k, v in timing_opportunity().items() if k != "signal_bar_time"}
        self.assertTrue(EntryTimer().evaluate(opp, quote())["approved"])
        strict = EntryTimer(config=EntryTimingConfig(require_signal_time=True)).evaluate(opp, quote())
        self.assertEqual((strict["status"], strict["reason"]), ("REJECTED", "SIGNAL_TIME_UNAVAILABLE"))
        self.assertTrue(EntryTimer(config=EntryTimingConfig(require_signal_time=True))
                        .evaluate(timing_opportunity(), quote())["approved"])

    def test_supplied_quality_overrides_the_computed_one_unless_ignored(self):
        market = quote(entry_quality=0.1)
        default = EntryTimer().evaluate(timing_opportunity(), market)
        self.assertEqual((default["reason"], default["quality_source"]), ("ENTRY_QUALITY_TOO_LOW", "SUPPLIED"))
        ignored = EntryTimer(config=EntryTimingConfig(ignore_supplied_quality=True)).evaluate(
            timing_opportunity(), market)
        self.assertTrue(ignored["approved"])
        self.assertEqual(ignored["quality_source"], "COMPUTED")


class RiskOptionTests(TestCase):
    def test_strict_structural_refuses_the_silent_stop_fallback(self):
        market = {**structural_market(), "stop_loss_points": 300}
        del market["point"]
        default = RiskManager().evaluate(market, True, opportunity=risk_opportunity())
        self.assertTrue(default["approved"])
        self.assertEqual(default["stop_source"], "SUPPLIED")
        strict = RiskManager(config=RiskConfig(strict_structural=True)).evaluate(
            market, True, opportunity=risk_opportunity())
        self.assertFalse(strict["approved"])
        self.assertEqual(strict["reason"], "MISSING_OR_INVALID_RISK_INPUT")

    def test_short_stop_moves_up_by_the_spread_and_longs_are_unchanged(self):
        adjust = RiskManager(config=RiskConfig(short_stop_spread_adjust=True))
        plain = RiskManager()
        spread = 0.2
        sell, sell_plain = (m.evaluate(structural_market(), True, opportunity=risk_opportunity("SELL"))
                            for m in (adjust, plain))
        self.assertAlmostEqual(sell["stop_price"] - sell_plain["stop_price"], spread, places=6)
        buy, buy_plain = (m.evaluate(structural_market(), True, opportunity=risk_opportunity("BUY"))
                          for m in (adjust, plain))
        self.assertEqual(buy["stop_price"], buy_plain["stop_price"])
        self.assertLessEqual(sell["estimated_risk"], sell["risk_budget"])

    def test_short_stop_adjustment_needs_a_valid_quote(self):
        market = structural_market()
        del market["ask"]
        result = RiskManager(config=RiskConfig(short_stop_spread_adjust=True)).evaluate(
            market, True, opportunity=risk_opportunity("SELL"))
        self.assertEqual(result["reason"], "MISSING_OR_INVALID_RISK_INPUT")
