from unittest import TestCase

from ramon_pipeline.signalflow.entry_timing import EntryTimer, EntryTimingConfig
from ramon_pipeline.signalflow.opportunities import OpportunityDetector
from ramon_pipeline.signalflow.pipeline import SignalFlowPipeline

from .test_opportunities import LONG_SETUP, SHORT_SETUP, make_bars, snapshot

T0 = 1_700_000_000
BAR = 900


def opportunity(side="BUY", **setup_overrides):
    """A hand-built approved opportunity: ATR 3, signal close 100, invalidation 4 below/above."""
    sign = 1 if side == "BUY" else -1
    setup = {"atr": 3.0, "reference_price": 100.0, "invalidation_price": 100.0 - 4.0 * sign}
    setup.update(setup_overrides)
    return {"approved": True, "side": side, "signal_id": "sig1", "signal_bar_time": T0, "setup": setup}


def quote(bid=100.0, ask=100.2, **extra):
    return {"bid": bid, "ask": ask, "point": 0.01, "latest_bar_time": T0, "timeframe": "M15", **extra}


class LegacyEntryTimingTests(TestCase):
    """The original entry_quality / spread_points contract must keep working."""

    legacy = {"approved": True, "side": "BUY", "score": 0.8}

    def test_good_metrics_are_approved(self):
        result = EntryTimer().evaluate(self.legacy, {"entry_quality": 0.8, "spread_points": 20})
        self.assertTrue(result["approved"])
        self.assertEqual(result["reason"], "ENTRY_TIMING_APPROVED")
        self.assertEqual((result["quality"], result["spread_points"]), (0.8, 20.0))
        self.assertEqual((result["status"], result["timing"]), ("ACCEPTED", "ENTER_NOW"))

    def test_original_reason_codes_are_preserved(self):
        timer = EntryTimer()
        cases = (
            ({"entry_quality": 0.3, "spread_points": 20}, "ENTRY_QUALITY_TOO_LOW", "DEFERRED"),
            ({"entry_quality": 1.5, "spread_points": 20}, "ENTRY_QUALITY_TOO_LOW", "DEFERRED"),
            ({"entry_quality": 0.9, "spread_points": 80}, "SPREAD_OUT_OF_RANGE", "DEFERRED"),
            ({"entry_quality": 0.9, "spread_points": -1}, "SPREAD_OUT_OF_RANGE", "DEFERRED"),
            ({"entry_quality": 0.9}, "SPREAD_OUT_OF_RANGE", "DEFERRED"),
            ({"entry_quality": "abc", "spread_points": 20}, "INVALID_ENTRY_METRICS", "REJECTED"),
            ({"entry_quality": 0.9, "spread_points": None}, "INVALID_ENTRY_METRICS", "REJECTED"),
        )
        for market, reason, status in cases:
            result = timer.evaluate(self.legacy, market)
            self.assertFalse(result["approved"], market)
            self.assertEqual((result["reason"], result["status"]), (reason, status), market)

    def test_nan_metrics_are_rejected_instead_of_slipping_through(self):
        for market in ({"entry_quality": float("nan"), "spread_points": 20}, {"entry_quality": 0.9, "spread_points": float("nan")}):
            result = EntryTimer().evaluate(self.legacy, market)
            self.assertFalse(result["approved"])
            self.assertEqual(result["reason"], "INVALID_ENTRY_METRICS")

    def test_unapproved_or_malformed_opportunity_is_never_timed(self):
        for opp in ({"approved": False}, {}, None):
            result = EntryTimer().evaluate(opp, {"entry_quality": 0.9, "spread_points": 10})
            self.assertFalse(result["approved"])
            self.assertEqual(result["reason"], "NO_APPROVED_OPPORTUNITY")
            self.assertEqual(result["timing"], "SKIP")

    def test_constructor_validation_is_unchanged(self):
        for kwargs in ({"minimum_quality": 1.2}, {"maximum_spread_points": 0}):
            with self.assertRaises(ValueError):
                EntryTimer(**kwargs)


class StructuredEntryTimingTests(TestCase):
    def evaluate(self, opp=None, market=None, **timer_kwargs):
        return EntryTimer(**timer_kwargs).evaluate(opp or opportunity(), market or quote())

    def test_fresh_setup_with_a_tight_quote_is_approved_and_explained(self):
        result = self.evaluate()
        self.assertTrue(result["approved"])
        self.assertEqual((result["status"], result["timing"], result["side"]), ("ACCEPTED", "ENTER_NOW", "BUY"))
        self.assertEqual(result["quality_source"], "COMPUTED")
        self.assertEqual(result["price_checks"], "APPLIED")
        self.assertAlmostEqual(result["spread_points"], 20.0, places=6)
        self.assertEqual(result["quote_price"], 100.2)
        self.assertTrue(all(check["passed"] for check in result["checks"]))
        self.assertIn("BUY entry timing approved", result["explanation"])

    def test_sell_setup_uses_the_bid_and_mirrored_checks(self):
        result = self.evaluate(opportunity("SELL"), quote(bid=99.8, ask=100.0))
        self.assertTrue(result["approved"])
        self.assertEqual(result["side"], "SELL")
        self.assertEqual(result["quote_price"], 99.8)
        # chase for a short is measured downwards from the signal close
        chased = self.evaluate(opportunity("SELL"), quote(bid=98.0, ask=98.2))
        self.assertEqual(chased["reason"], "PRICE_EXTENDED_FROM_SIGNAL")

    def test_supplied_entry_quality_is_used_when_present(self):
        result = self.evaluate(market=quote(entry_quality=0.9))
        self.assertEqual((result["quality"], result["quality_source"]), (0.9, "SUPPLIED"))
        low = self.evaluate(market=quote(entry_quality=0.2))
        self.assertEqual((low["reason"], low["status"], low["timing"]), ("ENTRY_QUALITY_TOO_LOW", "DEFERRED", "WAIT"))

    def test_price_that_ran_away_from_the_signal_is_deferred(self):
        result = self.evaluate(market=quote(bid=101.7, ask=101.9))  # 0.63 ATR beyond the close
        self.assertFalse(result["approved"])
        self.assertEqual((result["reason"], result["status"], result["timing"]), ("PRICE_EXTENDED_FROM_SIGNAL", "DEFERRED", "WAIT"))
        self.assertGreater(result["chase_atr"], 0.5)

    def test_price_retracing_below_the_signal_close_is_not_chasing(self):
        result = self.evaluate(market=quote(bid=99.0, ask=99.2))
        self.assertTrue(result["approved"])
        self.assertLess(result["chase_atr"], 0)

    def test_setup_invalidated_by_price_is_rejected(self):
        result = self.evaluate(market=quote(bid=95.9, ask=96.1))  # bid through the 96.0 level
        self.assertEqual((result["reason"], result["status"], result["timing"]), ("SETUP_INVALIDATED", "REJECTED", "SKIP"))
        short = self.evaluate(opportunity("SELL"), quote(bid=103.9, ask=104.1))
        self.assertEqual(short["reason"], "SETUP_INVALIDATED")

    def test_entry_right_next_to_the_invalidation_level_is_rejected(self):
        opp = opportunity(invalidation_price=99.5)  # only 0.23 ATR below the ask
        result = self.evaluate(opp, quote(bid=99.8, ask=100.0))
        self.assertEqual(result["reason"], "TOO_CLOSE_TO_INVALIDATION")

    def test_spread_is_judged_against_volatility(self):
        # 60 point (0.6) spread is 20%+ of a 2.5 ATR once the absolute limit is relaxed
        opp = opportunity(atr=2.5)
        result = self.evaluate(opp, quote(bid=100.0, ask=100.6), maximum_spread_points=500)
        self.assertEqual((result["reason"], result["status"]), ("SPREAD_TOO_WIDE_FOR_VOLATILITY", "DEFERRED"))

    def test_spread_is_judged_against_the_risk_distance(self):
        opp = opportunity(invalidation_price=99.0)  # 1.2 from the ask
        result = self.evaluate(opp, quote(bid=100.0, ask=100.4))  # 0.4 spread = 29% of the risk distance
        self.assertEqual(result["reason"], "SPREAD_TOO_LARGE_VS_RISK")
        self.assertEqual(result["status"], "DEFERRED")

    def test_absolute_spread_limit_still_applies(self):
        result = self.evaluate(market=quote(bid=100.0, ask=100.6))  # 60 points > 50
        self.assertEqual(result["reason"], "SPREAD_OUT_OF_RANGE")

    def test_computed_quality_ranks_cheaper_entries_higher(self):
        tight = self.evaluate(market=quote(bid=100.0, ask=100.05))
        mid = self.evaluate(market=quote(bid=100.2, ask=100.5))  # 30 points, price 0.17 ATR past the close
        edge = self.evaluate(market=quote(bid=101.0, ask=101.45), maximum_spread_points=500, minimum_quality=0.0)
        self.assertTrue(tight["approved"] and mid["approved"])
        self.assertGreater(tight["quality"], mid["quality"])
        self.assertGreater(mid["quality"], 0.65)  # a realistic spread is not penalised into a rejection
        self.assertLess(edge["quality"], mid["quality"])

    def test_quality_threshold_gates_marginal_entries(self):
        result = self.evaluate(market=quote(bid=100.2, ask=100.5), minimum_quality=0.95)
        self.assertEqual((result["reason"], result["status"]), ("ENTRY_QUALITY_TOO_LOW", "DEFERRED"))


class FreshnessTests(TestCase):
    def evaluate(self, latest, **timer_kwargs):
        return EntryTimer(**timer_kwargs).evaluate(opportunity(), quote(latest_bar_time=latest))

    def test_signal_on_the_latest_bar_is_fresh(self):
        result = self.evaluate(T0)
        self.assertTrue(result["approved"])
        self.assertEqual(result["signal_age_bars"], 0.0)

    def test_one_bar_old_is_still_allowed_by_default_but_two_is_expired(self):
        self.assertTrue(self.evaluate(T0 + BAR)["approved"])
        expired = self.evaluate(T0 + 2 * BAR)
        self.assertEqual((expired["reason"], expired["status"], expired["timing"]), ("SIGNAL_EXPIRED", "REJECTED", "SKIP"))

    def test_freshness_window_is_configurable(self):
        strict = EntryTimingConfig(max_signal_age_bars=0)
        self.assertEqual(self.evaluate(T0 + BAR, config=strict)["reason"], "SIGNAL_EXPIRED")

    def test_a_signal_newer_than_the_latest_bar_is_rejected_as_possible_lookahead(self):
        result = self.evaluate(T0 - BAR)
        self.assertEqual(result["reason"], "SIGNAL_TIME_INVALID")
        self.assertEqual(result["status"], "REJECTED")

    def test_latest_bar_time_can_come_from_the_bars_themselves(self):
        market = quote()
        del market["latest_bar_time"]
        market["bars"] = [{"time": T0 + 3 * BAR}]
        self.assertEqual(EntryTimer().evaluate(opportunity(), market)["reason"], "SIGNAL_EXPIRED")

    def test_unparseable_time_is_rejected_and_missing_time_is_reported_not_hidden(self):
        self.assertEqual(self.evaluate("garbage")["reason"], "SIGNAL_TIME_INVALID")
        market = quote()
        del market["latest_bar_time"]
        result = EntryTimer().evaluate(opportunity(), market)
        self.assertTrue(result["approved"])
        self.assertIn("skipped", next(c for c in result["checks"] if c["name"] == "freshness")["detail"])


class InvalidInputTests(TestCase):
    def reject(self, opp, market, reason, **timer_kwargs):
        result = EntryTimer(**timer_kwargs).evaluate(opp, market)
        self.assertFalse(result["approved"], result)
        self.assertEqual(result["reason"], reason)
        self.assertEqual((result["status"], result["timing"], result["side"]), ("REJECTED", "SKIP", "HOLD"))

    def test_invalid_quotes(self):
        for market in (
            quote(bid=100.2, ask=100.0),
            quote(bid=float("nan")),
            quote(bid=-1.0, ask=1.0),
            quote(bid="x"),
            quote(point=0),
            {**quote(), "point": None},
            quote(ask=float("inf")),
        ):
            self.reject(opportunity(), market, "INVALID_QUOTE")

    def test_malformed_setup_from_upstream(self):
        for opp in (
            opportunity(atr=0),
            opportunity(atr=float("nan")),
            opportunity(reference_price=-5),
            opportunity(invalidation_price=101.0),  # wrong side for a BUY
            {**opportunity(), "side": "HOLD"},
        ):
            self.reject(opp, quote(), "INVALID_OPPORTUNITY_SETUP")
        broken = opportunity()
        del broken["setup"]["atr"]
        self.reject(broken, quote(), "INVALID_OPPORTUNITY_SETUP")

    def test_non_numeric_supplied_quality_or_spread(self):
        self.reject(opportunity(), quote(entry_quality="high"), "INVALID_ENTRY_METRICS")
        market = {"latest_bar_time": T0, "spread_points": "wide"}
        self.reject(opportunity(), market, "INVALID_ENTRY_METRICS")

    def test_require_quote_rejects_when_no_quote_is_supplied(self):
        market = {"latest_bar_time": T0, "spread_points": 20, "entry_quality": 0.9}
        self.reject(opportunity(), market, "QUOTE_REQUIRED", require_quote=True)

    def test_without_a_quote_price_checks_are_skipped_but_reported(self):
        market = {"latest_bar_time": T0, "spread_points": 20, "entry_quality": 0.9}
        result = EntryTimer().evaluate(opportunity(), market)
        self.assertTrue(result["approved"])
        self.assertEqual(result["price_checks"], "SKIPPED_NO_QUOTE")
        self.assertEqual(EntryTimer().evaluate(opportunity(), {**market, "spread_points": 90})["reason"], "SPREAD_OUT_OF_RANGE")


class SeparationAndPipelineTests(TestCase):
    def test_evaluation_is_stateless_and_exposes_an_idempotency_key(self):
        timer = EntryTimer()
        first, second = timer.evaluate(opportunity(), quote()), timer.evaluate(opportunity(), quote())
        self.assertEqual(first, second)
        self.assertEqual(first["idempotency_key"], "entry:sig1")

    def test_result_contains_no_order_or_sizing_fields(self):
        result = EntryTimer().evaluate(opportunity(), quote())
        self.assertFalse({"volume", "lots", "stop_loss", "take_profit", "order_type", "entry_price"} & set(result))

    def test_config_validation(self):
        for kwargs in ({"max_signal_age_bars": -1}, {"max_chase_atr": 0}, {"max_spread_atr": -1}):
            with self.assertRaises(ValueError):
                EntryTimingConfig(**kwargs)

    def pipeline_market(self, legs, last_close_offset=0.3, **extra):
        bars = make_bars(legs, start_price=2400.0 if legs is LONG_SETUP else 2600.0)
        close = bars[-1]["close"]
        market = snapshot(
            bars, bid=close, ask=close + last_close_offset, point=0.01,
            account_balance=10_000, risk_percent=0.5, stop_loss_points=300, point_value_per_lot=1.0,
            volume_step=0.01, min_volume=0.01, max_volume=1.0, **extra,
        )
        return market

    def test_real_opportunity_flows_through_entry_timing_and_risk(self):
        outcome = SignalFlowPipeline(opportunities=OpportunityDetector()).run(self.pipeline_market(LONG_SETUP))
        timing = outcome["stages"]["entry_timing"]
        self.assertTrue(timing["approved"], timing["explanation"])
        self.assertEqual(timing["signal_id"], outcome["stages"]["opportunities"]["signal_id"])
        self.assertEqual(outcome["action"], "BUY")
        self.assertFalse(outcome["execution_enabled"])

    def test_real_short_opportunity_flows_through(self):
        market = self.pipeline_market(SHORT_SETUP)
        market["bid"], market["ask"] = market["bars"][-1]["close"] - 0.3, market["bars"][-1]["close"]
        outcome = SignalFlowPipeline().run(market)
        self.assertEqual(outcome["action"], "SELL")
        self.assertEqual(outcome["stages"]["entry_timing"]["quote_price"], market["bid"])

    def test_wide_spread_stops_the_pipeline_before_risk_sizing(self):
        outcome = SignalFlowPipeline().run(self.pipeline_market(LONG_SETUP, last_close_offset=0.9))
        self.assertTrue(outcome["stages"]["opportunities"]["approved"])
        self.assertFalse(outcome["stages"]["entry_timing"]["approved"])
        self.assertEqual(outcome["stages"]["risk_management"]["reason"], "UPSTREAM_STAGE_REJECTED")
        self.assertEqual(outcome["action"], "HOLD")
