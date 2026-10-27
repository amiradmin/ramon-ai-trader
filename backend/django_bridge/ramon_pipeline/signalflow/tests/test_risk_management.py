from unittest import TestCase

from ramon_pipeline.signalflow.entry_timing import EntryTimer
from ramon_pipeline.signalflow.opportunities import OpportunityDetector
from ramon_pipeline.signalflow.pipeline import SignalFlowPipeline
from ramon_pipeline.signalflow.risk_management import RiskConfig, RiskManager

from .test_opportunities import LONG_SETUP, SHORT_SETUP, make_bars, snapshot

ACCOUNT = {
    "account_balance": 10_000, "risk_percent": 0.5, "point_value_per_lot": 1.0,
    "volume_step": 0.01, "min_volume": 0.01, "max_volume": 1.0,
}


def legacy_market(**overrides):
    return {**ACCOUNT, "stop_loss_points": 300, **overrides}


def opportunity(side="BUY", **setup_overrides):
    """Approved setup: ATR 3, signal close 100, invalidation 4 below (BUY) / above (SELL)."""
    sign = 1 if side == "BUY" else -1
    setup = {"atr": 3.0, "reference_price": 100.0, "invalidation_price": 100.0 - 4.0 * sign}
    setup.update(setup_overrides)
    return {"approved": True, "side": side, "signal_id": "sig1", "setup": setup}


def structural_market(**overrides):
    return {**ACCOUNT, "point": 0.01, "bid": 100.0, "ask": 100.2, **overrides}


class LegacyRiskTests(TestCase):
    def test_original_sizing_is_unchanged(self):
        result = RiskManager().evaluate(legacy_market(), True)
        self.assertTrue(result["approved"])
        self.assertEqual(result["reason"], "RISK_APPROVED")
        self.assertEqual(result["volume"], 0.16)  # 50 budget / 300 points = 0.1666 -> floored to the step
        self.assertEqual(result["risk_budget"], 50.0)
        self.assertEqual(result["estimated_risk"], 48.0)
        self.assertEqual(result["risk_percent"], 0.5)
        self.assertEqual((result["status"], result["stop_source"]), ("ACCEPTED", "SUPPLIED"))

    def test_requested_risk_is_capped_and_flagged(self):
        result = RiskManager().evaluate(legacy_market(risk_percent=5.0), True)
        self.assertEqual(result["risk_percent"], 0.5)
        self.assertTrue(result["risk_capped"])

    def test_volume_is_capped_by_the_broker_maximum(self):
        result = RiskManager(maximum_risk_percent=100).evaluate(legacy_market(risk_percent=100, max_volume=0.5), True)
        self.assertEqual(result["volume"], 0.5)
        self.assertLess(result["estimated_risk"], result["risk_budget"])

    def test_budget_below_the_minimum_volume_is_refused_not_rounded_up(self):
        result = RiskManager().evaluate(legacy_market(account_balance=100), True)
        self.assertFalse(result["approved"])
        self.assertEqual(result["reason"], "RISK_BUDGET_BELOW_MINIMUM_VOLUME")
        self.assertEqual((result["volume"], result["risk_budget"]), (0.0, 0.5))

    def test_original_reason_codes_for_bad_inputs(self):
        manager = RiskManager()
        missing = legacy_market()
        del missing["account_balance"]
        cases = (
            (missing, "MISSING_OR_INVALID_RISK_INPUT"),
            (legacy_market(stop_loss_points="wide"), "MISSING_OR_INVALID_RISK_INPUT"),
            (legacy_market(stop_loss_points=None), "MISSING_OR_INVALID_RISK_INPUT"),
            (legacy_market(account_balance=float("nan")), "NON_FINITE_RISK_INPUT"),
            (legacy_market(stop_loss_points=float("inf")), "NON_FINITE_RISK_INPUT"),
            (legacy_market(stop_loss_points=0), "NON_POSITIVE_OR_INCONSISTENT_RISK_INPUT"),
            (legacy_market(account_balance=-5), "NON_POSITIVE_OR_INCONSISTENT_RISK_INPUT"),
            (legacy_market(max_volume=0.001), "NON_POSITIVE_OR_INCONSISTENT_RISK_INPUT"),
        )
        for market, reason in cases:
            result = manager.evaluate(market, True)
            self.assertFalse(result["approved"], market)
            self.assertEqual((result["reason"], result["volume"], result["status"]), (reason, 0.0, "REJECTED"), market)

    def test_upstream_rejection_and_bad_snapshot_never_size(self):
        for upstream, market in ((False, legacy_market()), (True, None)):
            result = RiskManager().evaluate(market, upstream)
            self.assertFalse(result["approved"])
            self.assertEqual(result["volume"], 0.0)
        self.assertEqual(RiskManager().evaluate(legacy_market(), False)["reason"], "UPSTREAM_STAGE_REJECTED")

    def test_constructor_validation_is_unchanged(self):
        for value in (0, -1, 101):
            with self.assertRaises(ValueError):
                RiskManager(maximum_risk_percent=value)

    def test_pipeline_still_calls_it_with_the_old_signature(self):
        outcome = SignalFlowPipeline().run(
            {**legacy_market(), "trend": "BUY", "setup_score": 0.9, "entry_quality": 0.9, "spread_points": 20}
        )
        self.assertTrue(outcome["stages"]["risk_management"]["approved"])
        self.assertEqual(outcome["action"], "BUY")


class StructuralStopTests(TestCase):
    def evaluate(self, opp=None, market=None, entry=None, **kwargs):
        return RiskManager(**kwargs).evaluate(market or structural_market(), True, opportunity=opp or opportunity(), entry=entry)

    def test_buy_stop_sits_beyond_the_invalidation_level(self):
        result = self.evaluate(entry={"quote_price": 100.2})
        self.assertTrue(result["approved"], result["explanation"])
        self.assertEqual(result["stop_price"], 95.25)  # 96.0 invalidation - 0.25 ATR buffer
        self.assertAlmostEqual(result["stop_points"], 495.0, places=3)
        self.assertEqual((result["volume"], result["estimated_risk"]), (0.10, 49.5))
        self.assertEqual((result["stop_source"], result["entry_price_source"]), ("STRUCTURAL", "TIMING_QUOTE"))
        self.assertEqual(result["side"], "BUY")
        self.assertLess(result["stop_price"], 96.0)
        self.assertIn("structural stop", result["explanation"])

    def test_sell_mirrors_the_stop_above_the_invalidation_level(self):
        result = self.evaluate(opportunity("SELL"), structural_market(bid=99.8, ask=100.0))
        self.assertTrue(result["approved"], result["explanation"])
        self.assertEqual(result["stop_price"], 104.75)
        self.assertEqual(result["entry_price_source"], "MARKET_QUOTE")  # the bid for a short
        self.assertAlmostEqual(result["stop_points"], 495.0, places=3)

    def test_entry_price_falls_back_to_the_signal_close_and_says_so(self):
        market = structural_market()
        del market["bid"], market["ask"]
        result = self.evaluate(market=market)
        self.assertEqual(result["entry_price_source"], "SIGNAL_REFERENCE")
        self.assertAlmostEqual(result["stop_points"], 475.0, places=3)

    def test_structural_stop_replaces_a_supplied_stop_loss(self):
        result = self.evaluate(market=structural_market(stop_loss_points=50))
        self.assertEqual(result["stop_source"], "STRUCTURAL")
        self.assertGreater(result["stop_points"], 400)

    def test_wider_structure_means_smaller_volume_at_constant_risk(self):
        near = self.evaluate(opportunity(invalidation_price=97.0))
        far = self.evaluate(opportunity(invalidation_price=95.0))
        self.assertGreater(near["volume"], far["volume"])
        self.assertLessEqual(near["estimated_risk"], near["risk_budget"])
        self.assertLessEqual(far["estimated_risk"], far["risk_budget"])

    def test_stop_too_tight_or_too_wide_for_the_volatility(self):
        tight = self.evaluate(opportunity(invalidation_price=99.9))  # ~0.35 ATR
        self.assertEqual(tight["reason"], "STOP_TOO_TIGHT")
        wide = self.evaluate(opportunity(invalidation_price=90.0))  # ~3.7 ATR
        self.assertEqual(wide["reason"], "STOP_TOO_WIDE")
        self.assertEqual((tight["volume"], wide["status"]), (0.0, "REJECTED"))

    def test_entry_already_beyond_the_stop_is_refused(self):
        result = self.evaluate(entry={"quote_price": 95.0})
        self.assertEqual(result["reason"], "ENTRY_BEYOND_STOP")

    def test_broker_minimum_stop_distance(self):
        result = self.evaluate(market=structural_market(stop_level_points=600))
        self.assertEqual(result["reason"], "STOP_BELOW_BROKER_MINIMUM")
        self.assertTrue(self.evaluate(market=structural_market(stop_level_points=400))["approved"])

    def test_malformed_or_unapproved_opportunity_is_refused(self):
        for opp, reason in (
            (opportunity(atr=0), "INVALID_OPPORTUNITY_SETUP"),
            (opportunity(atr=float("nan")), "INVALID_OPPORTUNITY_SETUP"),
            (opportunity(invalidation_price=101.0), "INVALID_OPPORTUNITY_SETUP"),
            ({**opportunity(), "side": "HOLD"}, "INVALID_OPPORTUNITY_SETUP"),
            ({**opportunity(), "approved": False}, "UPSTREAM_STAGE_REJECTED"),
            ({**opportunity(), "setup": {"atr": 3.0}}, "MISSING_OR_INVALID_RISK_INPUT"),
        ):
            self.assertEqual(self.evaluate(opp)["reason"], reason, opp)
        market = structural_market()
        del market["point"]
        self.assertEqual(self.evaluate(market=market)["reason"], "MISSING_OR_INVALID_RISK_INPUT")

    def test_risk_cannot_approve_what_upstream_rejected(self):
        result = RiskManager().evaluate(structural_market(), False, opportunity=opportunity())
        self.assertEqual((result["approved"], result["reason"]), (False, "UPSTREAM_STAGE_REJECTED"))

    def test_result_contains_no_order_instructions(self):
        result = self.evaluate()
        self.assertFalse({"order_type", "take_profit", "entry_price", "magic"} & set(result))


class AccountGuardTests(TestCase):
    def evaluate(self, **market):
        return RiskManager().evaluate(structural_market(**market), True, opportunity=opportunity())

    def test_reward_risk_is_enforced_only_when_a_target_is_supplied(self):
        self.assertTrue(self.evaluate()["approved"])
        self.assertIsNone(self.evaluate()["reward_risk"])
        low = self.evaluate(take_profit_points=500)  # ~1.0 reward:risk
        self.assertEqual((low["reason"], low["status"]), ("REWARD_RISK_TOO_LOW", "REJECTED"))
        good = self.evaluate(take_profit_points=800)
        self.assertTrue(good["approved"])
        self.assertAlmostEqual(good["reward_risk"], 1.616, places=3)

    def test_daily_loss_limit_halts_new_trades(self):
        self.assertEqual(self.evaluate(daily_loss_percent=3.0)["reason"], "DAILY_LOSS_LIMIT_REACHED")
        self.assertTrue(self.evaluate(daily_loss_percent=1.0)["approved"])

    def test_portfolio_risk_cap_defers_or_shrinks(self):
        full = self.evaluate(open_risk_percent=2.0)
        self.assertEqual((full["reason"], full["status"]), ("PORTFOLIO_RISK_LIMIT_REACHED", "DEFERRED"))
        shrunk = self.evaluate(open_risk_percent=1.8)  # only 0.2% headroom left
        self.assertTrue(shrunk["approved"])
        self.assertEqual(shrunk["risk_percent"], 0.2)
        self.assertTrue(shrunk["risk_capped"])
        self.assertEqual(shrunk["volume"], 0.04)

    def test_invalid_guard_inputs_are_rejected_not_ignored(self):
        self.assertEqual(self.evaluate(daily_loss_percent="lots")["reason"], "MISSING_OR_INVALID_RISK_INPUT")
        self.assertEqual(self.evaluate(open_risk_percent=float("nan"))["reason"], "NON_FINITE_RISK_INPUT")
        self.assertEqual(self.evaluate(take_profit_points=-5)["reason"], "NON_POSITIVE_OR_INCONSISTENT_RISK_INPUT")
        self.assertEqual(self.evaluate(stop_level_points=float("inf"))["reason"], "NON_FINITE_RISK_INPUT")

    def test_skipped_guards_are_reported_not_silently_passed(self):
        details = {c["name"]: c["detail"] for c in self.evaluate()["checks"]}
        for name in ("reward_risk", "daily_loss", "portfolio_risk"):
            self.assertIn("skipped", details[name])

    def test_guards_also_apply_to_the_legacy_stop(self):
        result = RiskManager().evaluate(legacy_market(daily_loss_percent=5), True)
        self.assertEqual(result["reason"], "DAILY_LOSS_LIMIT_REACHED")

    def test_config_validation(self):
        for kwargs in ({"stop_buffer_atr": -1}, {"min_stop_atr": 4.0}, {"min_reward_risk": 0}, {"max_daily_loss_percent": -1}):
            with self.assertRaises(ValueError):
                RiskConfig(**kwargs)


class FullChainTests(TestCase):
    def run_chain(self, legs, start_price, side_sign):
        bars = make_bars(legs, start_price=start_price)
        close = bars[-1]["close"]
        market = snapshot(bars, bid=close - (0.3 if side_sign < 0 else 0.0), ask=close + (0.0 if side_sign < 0 else 0.3), point=0.01, **ACCOUNT)
        opp = OpportunityDetector().evaluate(market)
        timing = EntryTimer().evaluate(opp, {**market, "entry_quality": 0.9})
        risk = RiskManager().evaluate(market, bool(opp["approved"] and timing["approved"]), opportunity=opp, entry=timing)
        return opp, timing, risk

    def test_long_chain_places_the_stop_beyond_the_pullback_low(self):
        opp, timing, risk = self.run_chain(LONG_SETUP, 2400.0, +1)
        self.assertTrue(opp["approved"] and timing["approved"] and risk["approved"], risk["explanation"])
        self.assertLess(risk["stop_price"], opp["setup"]["invalidation_price"])
        self.assertLess(risk["stop_price"], timing["quote_price"])
        self.assertLessEqual(risk["estimated_risk"], risk["risk_budget"])

    def test_short_chain_places_the_stop_beyond_the_pullback_high(self):
        opp, timing, risk = self.run_chain(SHORT_SETUP, 2600.0, -1)
        self.assertTrue(risk["approved"], risk["explanation"])
        self.assertEqual(risk["side"], "SELL")
        self.assertGreater(risk["stop_price"], opp["setup"]["invalidation_price"])
        self.assertGreater(risk["stop_price"], timing["quote_price"])

    def test_a_rejected_opportunity_is_never_sized(self):
        bars = make_bars([(10, 3), (10, -3)] * 10)
        market = snapshot(bars, bid=2400.0, ask=2400.3, point=0.01, **ACCOUNT)
        opp = OpportunityDetector().evaluate(market)
        timing = EntryTimer().evaluate(opp, market)
        risk = RiskManager().evaluate(market, bool(opp["approved"] and timing["approved"]), opportunity=opp, entry=timing)
        self.assertEqual((risk["approved"], risk["volume"], risk["reason"]), (False, 0.0, "UPSTREAM_STAGE_REJECTED"))
