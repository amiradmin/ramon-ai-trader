import threading
from unittest import TestCase

from ramon_pipeline.signalflow.learning import InMemoryOutcomeStore, Learning
from ramon_pipeline.signalflow.pipeline import SignalFlowPipeline

from .test_opportunities import LONG_SETUP, make_bars, snapshot
from .test_risk_management import ACCOUNT

T = 1_700_000_000


def outcome(trade_id="t1", pnl=10.0, closed=None, **extra):
    data = {"trade_id": trade_id, "realized_pnl": pnl, **extra}
    if closed is not None:
        data["closed_at"] = closed
    return data


def recorded(*outcomes):
    learning = Learning(InMemoryOutcomeStore())
    for item in outcomes:
        assert learning.observe(item)["accepted"], item
    return learning


class LegacyContractTests(TestCase):
    def test_valid_outcomes_keep_their_original_shape(self):
        for pnl, label in ((12.5, "WIN"), (-3.0, "LOSS"), (0.0, "BREAKEVEN")):
            result = Learning().observe({"trade_id": " abc ", "realized_pnl": pnl})
            self.assertTrue(result["accepted"])
            self.assertEqual((result["trade_id"], result["realized_pnl"], result["result"]), ("abc", pnl, label))
            self.assertEqual(result["reason"], "OUTCOME_VALIDATED_NOT_PERSISTED")

    def test_original_rejection_reasons_and_precedence(self):
        learning = Learning()
        self.assertEqual(learning.observe(None), {"accepted": False, "reason": "NO_OUTCOME_PROVIDED"})
        self.assertEqual(learning.observe({"trade_id": "a"})["reason"], "INVALID_OUTCOME")
        self.assertEqual(learning.observe({"trade_id": "a", "realized_pnl": "abc"})["reason"], "INVALID_OUTCOME")
        self.assertEqual(learning.observe({"realized_pnl": 1})["reason"], "MISSING_TRADE_ID")
        self.assertEqual(learning.observe({"trade_id": "  ", "realized_pnl": 1})["reason"], "MISSING_TRADE_ID")
        # a bad pnl is reported before a missing id, as before
        self.assertEqual(learning.observe({"realized_pnl": "x"})["reason"], "INVALID_OUTCOME")

    def test_non_finite_pnl_is_rejected_instead_of_counting_as_breakeven(self):
        for value in (float("nan"), float("inf"), float("-inf"), True):
            result = Learning().observe({"trade_id": "a", "realized_pnl": value})
            self.assertFalse(result["accepted"], value)
            self.assertEqual(result["reason"], "INVALID_OUTCOME")

    def test_non_mapping_outcome_is_rejected_not_raised(self):
        self.assertEqual(Learning().observe(["x"])["reason"], "INVALID_OUTCOME")

    def test_default_pipeline_still_validates_without_persisting(self):
        outcome_result = SignalFlowPipeline().run({"trend": "HOLD"}, outcome("p1", 5))["stages"]["learning"]
        self.assertEqual(outcome_result["reason"], "OUTCOME_VALIDATED_NOT_PERSISTED")


class OptionalFieldValidationTests(TestCase):
    def reject(self, **extra):
        result = Learning().observe(outcome(**extra))
        self.assertFalse(result["accepted"], extra)
        self.assertEqual(result["reason"], "INVALID_OUTCOME")
        return result

    def test_bad_optional_fields_are_rejected_with_a_detail(self):
        self.reject(opened_at=T + 100, closed_at=T)  # closed before opened
        self.reject(closed_at="not a time")
        self.reject(risk_amount=0)
        self.reject(risk_amount=-5)
        self.reject(risk_amount="x")
        self.reject(risk_amount=float("nan"))
        self.reject(side="LONG")
        self.reject(forecast_confirmed="yes")
        self.reject(score=float("inf"))
        self.assertIn("closed_at", self.reject(opened_at=T + 100, closed_at=T)["detail"])

    def test_r_multiple_tags_and_iso_times(self):
        result = Learning().observe(
            outcome(pnl=-20, risk_amount=40, side="sell", regime="BEARISH", opened_at="2026-10-26T10:00:00+00:00",
                    closed_at="2026-10-26T11:00:00Z", forecast_confirmed=True, unknown_field="ignored")
        )
        self.assertTrue(result["accepted"])
        self.assertEqual(result["r_multiple"], -0.5)
        self.assertEqual(result["tags"], {"side": "SELL", "regime": "BEARISH", "forecast_confirmed": True})

    def test_r_multiple_is_none_without_a_risk_amount(self):
        self.assertIsNone(Learning().observe(outcome())["r_multiple"])


class StoreTests(TestCase):
    def test_recording_duplicates_and_conflicts(self):
        learning = Learning(InMemoryOutcomeStore())
        self.assertEqual(learning.observe(outcome("a", 5, closed=T))["reason"], "OUTCOME_RECORDED")
        self.assertEqual(learning.observe(outcome("a", 5, closed=T))["reason"], "DUPLICATE_OUTCOME_IGNORED")
        conflict = learning.observe(outcome("a", -50, closed=T))
        self.assertFalse(conflict["accepted"])
        self.assertEqual(conflict["reason"], "CONFLICTING_OUTCOME")
        self.assertEqual(learning.summary()["overall"]["trades"], 1)
        self.assertEqual(learning.summary()["overall"]["net_pnl"], 5.0)  # the original was not overwritten

    def test_invalid_outcomes_are_never_stored(self):
        learning = Learning(InMemoryOutcomeStore())
        learning.observe({"trade_id": "a", "realized_pnl": float("nan")})
        learning.observe({"realized_pnl": 3})
        self.assertEqual(learning.summary()["overall"]["trades"], 0)

    def test_store_returns_copies_so_history_cannot_be_edited(self):
        store = InMemoryOutcomeStore()
        Learning(store).observe(outcome("a", 5, closed=T))
        store.all()[0]["realized_pnl"] = 9999
        store.get("a")["realized_pnl"] = 9999
        self.assertEqual(store.get("a")["realized_pnl"], 5.0)

    def test_concurrent_deliveries_are_recorded_once(self):
        learning = Learning(InMemoryOutcomeStore())
        threads = [threading.Thread(target=learning.observe, args=(outcome("same", 5, closed=T),)) for _ in range(20)]
        threads += [threading.Thread(target=learning.observe, args=(outcome(f"id{i}", 1, closed=T),)) for i in range(50)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(learning.summary()["overall"]["trades"], 51)

    def test_analysis_needs_a_store(self):
        with self.assertRaises(RuntimeError):
            Learning().summary()
        with self.assertRaises(RuntimeError):
            Learning().review()


class SummaryTests(TestCase):
    def test_statistics_match_a_hand_calculation(self):
        pnls = [10, -5, 10, -5, -5, 20]
        learning = recorded(*[outcome(f"t{i}", p, closed=T + i) for i, p in enumerate(pnls)])
        stats = learning.summary()["overall"]

        self.assertEqual((stats["trades"], stats["wins"], stats["losses"]), (6, 3, 3))
        self.assertEqual(stats["win_rate"], 0.5)
        self.assertAlmostEqual(stats["profit_factor"], 40 / 15)
        self.assertAlmostEqual(stats["expectancy"], 25 / 6)
        self.assertAlmostEqual(stats["payoff_ratio"], (40 / 3) / 5)
        self.assertAlmostEqual(stats["breakeven_win_rate"], 5 / (40 / 3 + 5))
        self.assertEqual(stats["max_drawdown"], 10.0)
        self.assertEqual(stats["max_consecutive_losses"], 2)
        low, high = stats["win_rate_ci95"]
        self.assertTrue(low < 0.5 < high and high - low > 0.4)  # six trades say very little

    def test_ordering_uses_close_time_not_arrival_order(self):
        # Arrival order would give a loss streak of 1; by close time the two losses are adjacent.
        learning = recorded(outcome("a", -10, closed=T), outcome("b", 10, closed=T + 20), outcome("c", -10, closed=T + 10))
        stats = learning.summary()["overall"]
        self.assertEqual(stats["max_consecutive_losses"], 2)
        self.assertEqual(stats["max_drawdown"], 20.0)

    def test_profit_factor_is_undefined_without_losses_and_empty_is_safe(self):
        self.assertIsNone(recorded(outcome("a", 5, closed=T)).summary()["overall"]["profit_factor"])
        empty = Learning(InMemoryOutcomeStore()).summary()["overall"]
        self.assertEqual((empty["trades"], empty["win_rate"], empty["expectancy"]), (0, None, None))

    def test_segments_are_reported(self):
        learning = recorded(
            outcome("a", 10, closed=T, side="BUY", regime="BULLISH", exit_reason="TP"),
            outcome("b", -5, closed=T + 1, side="SELL", regime="BEARISH", exit_reason="SL"),
            outcome("c", 7, closed=T + 2, side="BUY", regime="BULLISH", exit_reason="TP"),
        )
        summary = learning.summary()
        self.assertEqual(summary["by_side"]["BUY"]["trades"], 2)
        self.assertEqual(summary["by_regime"]["BEARISH"]["net_pnl"], -5.0)
        self.assertEqual(summary["by_exit_reason"]["TP"]["wins"], 2)

    def test_as_of_excludes_the_future_and_untimed_records(self):
        learning = recorded(
            outcome("a", 10, closed=T + 1000), outcome("b", -4, closed=T + 2000),
            outcome("c", 99, closed=T + 3000), outcome("d", 50),
        )
        before = learning.summary(as_of=T + 2000)
        self.assertEqual(before["overall"]["trades"], 2)
        self.assertEqual(before["overall"]["net_pnl"], 6.0)
        self.assertEqual(before["excluded_without_close_time"], 2)
        self.assertEqual(learning.summary()["overall"]["trades"], 4)
        # the same cut-off given as a datetime string behaves identically
        self.assertEqual(learning.summary(as_of="2023-11-14T22:13:20+00:00")["overall"]["trades"], 0)


class ReviewTests(TestCase):
    def test_small_samples_draw_no_conclusions(self):
        learning = recorded(*[outcome(f"t{i}", -10, closed=T + i) for i in range(10)])
        review = learning.review(min_samples=30)
        self.assertFalse(review["sufficient_data"])
        self.assertEqual([f["code"] for f in review["findings"]], ["INSUFFICIENT_DATA"])

    def test_high_win_rate_with_larger_losses_is_flagged(self):
        trades = [outcome(f"w{i}", 5, closed=T + 2 * i, risk_amount=10) for i in range(55)]
        trades += [outcome(f"l{i}", -10, closed=T + 2 * i + 1, risk_amount=10) for i in range(45)]
        review = recorded(*trades).review()
        codes = {f["code"] for f in review["findings"]}
        self.assertIn("NEGATIVE_EXPECTANCY_SIGNIFICANT", codes)
        self.assertIn("WIN_RATE_BELOW_PAYOFF_BREAKEVEN", codes)
        payoff = next(f for f in review["findings"] if f["code"] == "WIN_RATE_BELOW_PAYOFF_BREAKEVEN")
        self.assertAlmostEqual(payoff["evidence"]["breakeven_win_rate"], 10 / 15)

    def test_noise_is_reported_as_no_provable_edge_not_as_a_signal(self):
        pnls = [10, -10] * 20
        review = recorded(*[outcome(f"t{i}", p, closed=T + i) for i, p in enumerate(pnls)]).review()
        self.assertEqual([f["code"] for f in review["findings"]], ["EXPECTANCY_NOT_DISTINGUISHABLE_FROM_ZERO"])

    def test_a_losing_segment_is_flagged_only_with_enough_trades(self):
        buys = [outcome(f"b{i}", -10, closed=T + i, risk_amount=10, side="BUY") for i in range(40)]
        sells = [outcome(f"s{i}", 10, closed=T + 100 + i, risk_amount=10, side="SELL") for i in range(40)]
        review = recorded(*buys, *sells).review()
        segments = [f for f in review["findings"] if f["code"] == "SEGMENT_UNDERPERFORMS"]
        self.assertEqual([f["evidence"]["segment"] for f in segments], [{"side": "BUY"}])
        few = recorded(*buys[:5], *sells).review(min_samples=30)
        self.assertFalse([f for f in few["findings"] if f["code"] == "SEGMENT_UNDERPERFORMS"])

    def test_a_segment_that_is_only_probably_worse_is_not_flagged(self):
        # BUY mean is -0.3R with a 95% (but not 99%) bound below zero: too weak to single out a segment.
        buys = [outcome(f"b{i}", -13 if i % 2 else 7, closed=T + i, risk_amount=10, side="BUY") for i in range(40)]
        sells = [outcome(f"s{i}", 13 if i % 2 else -7, closed=T + 100 + i, risk_amount=10, side="SELL") for i in range(40)]
        review = recorded(*buys, *sells).review()
        self.assertFalse([f for f in review["findings"] if f["code"] == "SEGMENT_UNDERPERFORMS"])

    def test_review_respects_as_of_so_replays_cannot_see_the_future(self):
        losing = [outcome(f"l{i}", -10, closed=T + i, risk_amount=10) for i in range(40)]
        winning = [outcome(f"w{i}", 10, closed=T + 10_000 + i, risk_amount=10) for i in range(40)]
        learning = recorded(*losing, *winning)
        early = learning.review(as_of=T + 5_000)
        self.assertEqual(early["trades"], 40)
        self.assertIn("NEGATIVE_EXPECTANCY_SIGNIFICANT", {f["code"] for f in early["findings"]})
        self.assertNotIn("NEGATIVE_EXPECTANCY_SIGNIFICANT", {f["code"] for f in learning.review()["findings"]})

    def test_findings_are_advisory_and_change_nothing(self):
        trades = [outcome(f"l{i}", -10, closed=T + i, risk_amount=10) for i in range(40)]
        learning = recorded(*trades)
        before = learning.summary()
        review = learning.review()
        self.assertTrue(review["advisory"])
        self.assertFalse(review["auto_apply"])
        self.assertTrue(all(f["advisory"] for f in review["findings"]))
        self.assertEqual(learning.summary(), before)

    def test_min_samples_has_a_floor(self):
        with self.assertRaises(ValueError):
            recorded(outcome("a", 1, closed=T)).review(min_samples=3)


class PipelineIntegrationTests(TestCase):
    def test_context_from_a_real_run_links_the_outcome_to_its_signal(self):
        market = {**snapshot(make_bars(LONG_SETUP)), **ACCOUNT, "stop_loss_points": 300, "entry_quality": 0.9, "spread_points": 20}
        result = SignalFlowPipeline().run(market)
        self.assertEqual(result["action"], "BUY")
        context = Learning.build_outcome_context(result["stages"])
        self.assertEqual((context["side"], context["regime"], context["setup_type"]), ("BUY", "BULLISH", "TREND_PULLBACK"))
        self.assertEqual(context["signal_id"], result["stages"]["opportunities"]["signal_id"])
        self.assertFalse(context["forecast_confirmed"])

        learning = Learning(InMemoryOutcomeStore())
        closed = learning.observe({"trade_id": "ticket-9", "realized_pnl": -24.0, "closed_at": T, **context})
        self.assertTrue(closed["accepted"], closed)
        self.assertEqual(closed["r_multiple"], round(-24.0 / context["risk_amount"], 6))
        self.assertEqual(closed["tags"]["signal_id"], context["signal_id"])

    def test_context_skips_unapproved_opportunities(self):
        result = SignalFlowPipeline().run({"trend": "HOLD"})
        self.assertNotIn("side", Learning.build_outcome_context(result["stages"]))

    def test_pipeline_records_through_an_injected_store_and_is_idempotent(self):
        pipeline = SignalFlowPipeline(learning=Learning(InMemoryOutcomeStore()))
        first = pipeline.run({"trend": "HOLD"}, outcome("p1", 5, closed=T))["stages"]["learning"]
        again = pipeline.run({"trend": "HOLD"}, outcome("p1", 5, closed=T))["stages"]["learning"]
        self.assertEqual((first["reason"], again["reason"]), ("OUTCOME_RECORDED", "DUPLICATE_OUTCOME_IGNORED"))
        self.assertEqual(pipeline.learning.summary()["overall"]["trades"], 1)
