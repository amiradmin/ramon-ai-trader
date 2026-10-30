import math
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from ramon_pipeline.signalflow import opportunities as opportunities_module
from ramon_pipeline.signalflow.opportunities import OpportunityConfig, OpportunityDetector
from ramon_pipeline.signalflow.pipeline import SignalFlowPipeline


class OpportunityDetectorTests(TestCase):
    def test_legacy_market_signal_remains_supported_by_default(self):
        result = OpportunityDetector().evaluate(
            {"trend": "BUY", "setup_score": 0.8}
        )
        self.assertTrue(result["approved"])
        self.assertEqual(result["side"], "BUY")
        self.assertEqual(result["reason"], "OPPORTUNITY_FOUND")

    def test_forecasting_requires_a_symbol(self):
        result = OpportunityDetector().evaluate(
            {
                "trend": "BUY",
                "setup_score": 0.8,
                "use_statistical_forecasts": True,
            }
        )
        self.assertFalse(result["approved"])
        self.assertEqual(result["side"], "HOLD")
        self.assertEqual(
            result["forecasting"]["reason"], "FORECAST_SYMBOL_REQUIRED"
        )

    @patch("ramon_pipeline.signalflow.forecasting.sarima.forecast_market_bars_sarima")
    @patch("ramon_pipeline.signalflow.forecasting.arima.forecast_market_bars")
    def test_forecasts_must_agree_with_the_market_direction(
        self, arima_forecast, sarima_forecast
    ):
        arima_forecast.return_value = SimpleNamespace(
            last_close=100.0,
            forecast_close=102.0,
            as_dict=lambda: {"forecast_close": 102.0},
        )
        sarima_forecast.return_value = SimpleNamespace(
            last_close=100.0,
            forecast_close=101.0,
            as_dict=lambda: {"forecast_close": 101.0},
        )
        result = OpportunityDetector().evaluate(
            {
                "symbol": "XAUUSD_o",
                "timeframe": "M15",
                "trend": "BUY",
                "setup_score": 0.8,
                "use_statistical_forecasts": True,
            }
        )
        self.assertTrue(result["approved"])
        self.assertEqual(
            result["reason"], "OPPORTUNITY_FOUND_WITH_FORECAST_CONFIRMATION"
        )
        arima_forecast.assert_called_once()
        sarima_forecast.assert_called_once()

    @patch("ramon_pipeline.signalflow.forecasting.sarima.forecast_market_bars_sarima")
    @patch("ramon_pipeline.signalflow.forecasting.arima.forecast_market_bars")
    def test_conflicting_forecasts_reject_the_opportunity(
        self, arima_forecast, sarima_forecast
    ):
        arima_forecast.return_value = SimpleNamespace(
            last_close=100.0,
            forecast_close=102.0,
            as_dict=lambda: {"forecast_close": 102.0},
        )
        sarima_forecast.return_value = SimpleNamespace(
            last_close=100.0,
            forecast_close=98.0,
            as_dict=lambda: {"forecast_close": 98.0},
        )
        result = OpportunityDetector().evaluate(
            {
                "symbol": "XAUUSD_o",
                "timeframe": "M15",
                "trend": "BUY",
                "setup_score": 0.8,
                "use_statistical_forecasts": True,
            }
        )
        self.assertFalse(result["approved"])
        self.assertEqual(result["side"], "HOLD")
        self.assertEqual(
            result["reason"], "FORECAST_DIRECTION_MISMATCH"
        )


# ---------------------------------------------------------------------------
# Structural (market-structure) opportunity detection
# ---------------------------------------------------------------------------

ARIMA_PATH = "ramon_pipeline.signalflow.forecasting.arima.forecast_market_bars"
SARIMA_PATH = "ramon_pipeline.signalflow.forecasting.sarima.forecast_market_bars_sarima"


def make_bars(legs, start_price=2400.0, start_time=1_700_000_000, step=900):
    """Deterministic XAUUSD-like M15 candles; ``legs`` is [(bar_count, total_price_change)]."""
    bars, price, moment, index = [], start_price, start_time, 0
    for count, change in legs:
        for k in range(1, count + 1):
            close = price + change * k / count + 0.25 * math.sin(index * 1.9)
            bars.append(
                {
                    "time": moment,
                    "open": close,
                    "high": close + 0.7 + 0.2 * abs(math.sin(index * 0.7)),
                    "low": close - 0.7 - 0.2 * abs(math.cos(index * 0.9)),
                    "close": close,
                }
            )
            moment += step
            index += 1
        price += change
    return bars


UPTREND = [(14, 9), (6, -4), (14, 10), (6, -4.5), (14, 11), (6, -4.5), (14, 10), (6, -5)] * 2
DOWNTREND = [(10, -9), (5, 3.5), (10, -10), (5, 4), (10, -11), (5, 4), (10, -10), (5, 4)] * 2
# Impulse of ~6.5 ATR, 61% pullback into the EMA zone, then two bars of resumption.
LONG_SETUP = UPTREND + [(12, 11), (6, -6), (2, 2.5)]
SHORT_SETUP = [(count, -change) for count, change in LONG_SETUP]


def snapshot(bars, **extra):
    return {"symbol": "XAUUSD_o", "timeframe": "M15", "bars": bars, **extra}


def iso(epoch):
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()


def fake_forecast(last_close, forecast_close, last_bar_time=None, **extra):
    fields = {"last_close": last_close, "forecast_close": forecast_close, **extra}
    if last_bar_time is not None:
        fields["last_bar_time"] = last_bar_time
    return SimpleNamespace(as_dict=lambda: dict(fields), **fields)


class ValidSetupTests(TestCase):
    def test_bullish_pullback_resumption_is_accepted_and_explained(self):
        bars = make_bars(LONG_SETUP)
        result = OpportunityDetector().evaluate(snapshot(bars))

        self.assertTrue(result["approved"])
        self.assertEqual(result["status"], "ACCEPTED")
        self.assertEqual(result["side"], "BUY")
        self.assertEqual(result["reason"], "OPPORTUNITY_FOUND")
        self.assertEqual(result["regime"]["label"], "BULLISH")
        self.assertGreaterEqual(result["score"], 0.65)
        self.assertEqual(result["signal_bar_time"], bars[-1]["time"])
        self.assertEqual(result["setup"]["type"], "TREND_PULLBACK")
        self.assertTrue(all(check["passed"] for check in result["checks"]))
        self.assertIn("BUY accepted", result["explanation"])
        self.assertEqual(set(result["components"]), {"strength", "volatility", "trend", "pullback", "momentum"})

    def test_bearish_pullback_resumption_is_accepted_as_a_short(self):
        result = OpportunityDetector().evaluate(snapshot(make_bars(SHORT_SETUP, start_price=2600.0)))

        self.assertTrue(result["approved"])
        self.assertEqual(result["side"], "SELL")
        self.assertEqual(result["regime"]["label"], "BEARISH")
        # For a short the invalidation level is the pullback high, above the reference price.
        self.assertGreater(result["setup"]["invalidation_price"], result["setup"]["reference_price"])

    def test_opportunity_output_never_contains_execution_or_sizing_fields(self):
        result = OpportunityDetector().evaluate(snapshot(make_bars(LONG_SETUP)))
        forbidden = {"volume", "lots", "stop_loss", "take_profit", "order_type", "entry_price"}
        self.assertFalse(forbidden & set(result))
        self.assertFalse(forbidden & set(result["setup"]))

    def test_output_flows_through_the_whole_pipeline(self):
        market = snapshot(
            make_bars(LONG_SETUP),
            entry_quality=0.9,
            spread_points=20,
            account_balance=10_000,
            risk_percent=0.5,
            stop_loss_points=300,
            point_value_per_lot=1.0,
            volume_step=0.01,
            min_volume=0.01,
            max_volume=1.0,
        )
        outcome = SignalFlowPipeline().run(market)

        self.assertTrue(outcome["stages"]["opportunities"]["approved"])
        self.assertTrue(outcome["stages"]["entry_timing"]["approved"])
        self.assertTrue(outcome["stages"]["risk_management"]["approved"])
        self.assertEqual(outcome["action"], "BUY")
        self.assertFalse(outcome["execution_enabled"])

    def test_pipeline_holds_when_the_opportunity_is_not_approved(self):
        market = snapshot(make_bars(LONG_SETUP[:-1]), entry_quality=0.9, spread_points=20)
        outcome = SignalFlowPipeline().run(market)
        self.assertEqual(outcome["action"], "HOLD")
        self.assertFalse(outcome["approved"])
        self.assertEqual(outcome["stages"]["entry_timing"]["reason"], "NO_APPROVED_OPPORTUNITY")


class DeferredAndWeakSetupTests(TestCase):
    def evaluate(self, legs, **kwargs):
        return OpportunityDetector(**kwargs).evaluate(snapshot(make_bars(legs)))

    def test_trend_still_extending_is_deferred_until_a_pullback_starts(self):
        bars = make_bars(LONG_SETUP)
        result = OpportunityDetector().evaluate(snapshot(bars[:-6]))
        self.assertFalse(result["approved"])
        self.assertEqual(result["status"], "DEFERRED")
        self.assertEqual(result["reason"], "IMPULSE_STILL_EXTENDING")
        self.assertEqual(result["side"], "HOLD")
        self.assertEqual(result["candidate_side"], "BUY")

    def test_pullback_without_resumption_is_deferred(self):
        bars = make_bars(LONG_SETUP)
        result = OpportunityDetector().evaluate(snapshot(bars[:-3]))
        self.assertEqual(result["status"], "DEFERRED")
        self.assertEqual(result["reason"], "AWAITING_RESUMPTION")
        self.assertIn("waiting", result["explanation"])

    def test_shallow_pullback_is_deferred(self):
        result = self.evaluate(UPTREND + [(12, 11), (4, -2.0), (2, 1.5)])
        self.assertEqual(result["status"], "DEFERRED")
        self.assertEqual(result["reason"], "PULLBACK_NOT_DEEP_ENOUGH")

    def test_pullback_that_breaks_the_impulse_origin_is_rejected(self):
        result = self.evaluate(UPTREND + [(12, 11), (8, -10), (2, 2.5)])
        self.assertEqual(result["status"], "REJECTED")
        self.assertEqual(result["reason"], "PULLBACK_TOO_DEEP")

    def test_valid_geometry_below_the_score_threshold_is_rejected(self):
        result = self.evaluate(LONG_SETUP, minimum_score=0.99)
        self.assertEqual(result["status"], "REJECTED")
        self.assertEqual(result["reason"], "SETUP_SCORE_TOO_LOW")
        self.assertLess(result["score"], 0.99)
        self.assertEqual(result["checks"][-1]["name"], "score")

    def test_ranging_market_is_rejected_for_a_trend_following_strategy(self):
        result = self.evaluate([(10, 3), (10, -3)] * 10)
        self.assertEqual(result["status"], "REJECTED")
        self.assertEqual(result["reason"], "MARKET_RANGING")
        self.assertEqual(result["regime"]["label"], "RANGING")
        self.assertEqual(result["candidate_side"], "NONE")

    def test_volatility_spike_defers_and_dead_market_rejects(self):
        bars = make_bars(LONG_SETUP)
        spiked = [dict(bar) for bar in bars]
        for bar in spiked[-3:]:
            bar["high"] += 12.0
            bar["low"] -= 12.0
        spike = OpportunityDetector().evaluate(snapshot(spiked))
        self.assertEqual(spike["reason"], "VOLATILITY_SPIKE")
        self.assertEqual(spike["status"], "DEFERRED")

        quiet = OpportunityDetector(config=OpportunityConfig(atr_ratio_min=1.5)).evaluate(snapshot(bars))
        self.assertEqual(quiet["reason"], "VOLATILITY_TOO_LOW")
        self.assertEqual(quiet["status"], "REJECTED")

    def test_overextended_momentum_defers(self):
        result = self.evaluate(LONG_SETUP, config=OpportunityConfig(rsi_overbought=40.0))
        self.assertEqual(result["status"], "DEFERRED")
        self.assertEqual(result["reason"], "MOMENTUM_OVEREXTENDED")

    def test_stale_pullback_is_rejected(self):
        result = self.evaluate(LONG_SETUP, config=OpportunityConfig(max_bars_since_pullback_low=0))
        self.assertEqual(result["status"], "REJECTED")
        self.assertEqual(result["reason"], "SETUP_STALE")


class DuplicateSignalTests(TestCase):
    def test_same_pullback_does_not_signal_twice(self):
        bars = make_bars(LONG_SETUP + [(1, 1.0), (1, 0.8)])
        detector = OpportunityDetector()
        first = detector.evaluate(snapshot(bars[:-2]))
        second = detector.evaluate(snapshot(bars[:-1]))
        third = detector.evaluate(snapshot(bars))

        self.assertTrue(first["approved"])
        for later in (second, third):
            self.assertFalse(later["approved"])
            self.assertEqual(later["reason"], "DUPLICATE_SIGNAL")
            self.assertEqual(later["status"], "REJECTED")

    def test_reevaluating_the_same_bar_is_idempotent(self):
        bars = make_bars(LONG_SETUP)
        detector = OpportunityDetector()
        first = detector.evaluate(snapshot(bars))
        second = detector.evaluate(snapshot(bars))
        self.assertTrue(first["approved"] and second["approved"])
        self.assertEqual(first["signal_id"], second["signal_id"])

    def test_a_new_pullback_after_a_new_impulse_is_a_new_signal(self):
        detector = OpportunityDetector()
        first = detector.evaluate(snapshot(make_bars(LONG_SETUP)))
        second_legs = LONG_SETUP + [(1, 1.0), (1, 0.8), (12, 11), (6, -6), (2, 2.5)]
        second = detector.evaluate(snapshot(make_bars(second_legs)))
        self.assertTrue(first["approved"])
        self.assertTrue(second["approved"], second["explanation"])
        self.assertNotEqual(first["signal_id"], second["signal_id"])


class InvalidDataTests(TestCase):
    def setUp(self):
        self.bars = make_bars(LONG_SETUP)
        self.detector = OpportunityDetector()

    def reject(self, bars, reason, **extra):
        result = self.detector.evaluate(snapshot(bars, **extra))
        self.assertFalse(result["approved"])
        self.assertEqual(result["side"], "HOLD")
        self.assertEqual(result["reason"], reason)
        self.assertEqual(result["status"], "REJECTED" if reason != "LAST_BAR_NOT_CLOSED" else "DEFERRED")
        return result

    def test_too_few_bars(self):
        self.reject(self.bars[:40], "INSUFFICIENT_BARS")

    def test_non_finite_price(self):
        broken = [dict(bar) for bar in self.bars]
        broken[50]["close"] = float("nan")
        result = self.reject(broken, "INVALID_MARKET_DATA")
        self.assertIn("bar 50", result["explanation"])

    def test_non_numeric_and_boolean_prices(self):
        for value in ("abc", None, True):
            broken = [dict(bar) for bar in self.bars]
            broken[10]["high"] = value
            self.reject(broken, "INVALID_MARKET_DATA")

    def test_high_below_low(self):
        broken = [dict(bar) for bar in self.bars]
        broken[60]["high"] = broken[60]["low"] - 1.0
        self.reject(broken, "INVALID_MARKET_DATA")

    def test_close_outside_range_and_non_positive_prices(self):
        broken = [dict(bar) for bar in self.bars]
        broken[60]["close"] = broken[60]["high"] + 5.0
        self.reject(broken, "INVALID_MARKET_DATA")
        broken = [dict(bar) for bar in self.bars]
        broken[61]["low"] = 0.0
        self.reject(broken, "INVALID_MARKET_DATA")

    def test_missing_field_duplicate_timestamp_and_unordered_bars(self):
        broken = [dict(bar) for bar in self.bars]
        del broken[80]["close"]
        self.reject(broken, "INVALID_MARKET_DATA")
        broken = [dict(bar) for bar in self.bars]
        broken[70]["time"] = broken[69]["time"]
        self.reject(broken, "INVALID_MARKET_DATA")
        self.reject(list(reversed(self.bars)), "INVALID_MARKET_DATA")

    def test_bars_must_be_a_list_of_objects(self):
        self.reject("not-bars", "INVALID_MARKET_DATA")
        self.reject([1, 2, 3] * 50, "INVALID_MARKET_DATA")

    def test_recent_data_gap(self):
        gapped = [dict(bar) for bar in self.bars]
        for bar in gapped[-20:]:
            bar["time"] += 900 * 30
        self.reject(gapped, "DATA_GAP_IN_RECENT_BARS")

    def test_flat_prices_cannot_be_analysed(self):
        flat = [{"time": 1_700_000_000 + 900 * i, "high": 2400.0, "low": 2400.0, "close": 2400.0} for i in range(150)]
        self.reject(flat, "INVALID_MARKET_DATA")

    def test_forming_candle_is_deferred(self):
        self.reject(self.bars, "LAST_BAR_NOT_CLOSED", last_bar_closed=False)

    def test_unsupported_symbol_and_bad_snapshot(self):
        result = self.detector.evaluate({"symbol": "EURUSD", "bars": self.bars})
        self.assertEqual(result["reason"], "UNSUPPORTED_SYMBOL")
        self.assertEqual(self.detector.evaluate(None)["reason"], "INVALID_MARKET_SNAPSHOT")
        self.assertFalse(self.detector.evaluate(["x"])["approved"])

    def test_timestamps_may_be_datetimes_or_iso_strings(self):
        converted = [dict(bar, time=iso(bar["time"])) for bar in self.bars]
        result = self.detector.evaluate(snapshot(converted))
        self.assertTrue(result["approved"])

    def test_unexpected_errors_fail_closed(self):
        with patch.object(opportunities_module, "_compute_indicators", side_effect=RuntimeError("boom")):
            with self.assertLogs(opportunities_module.logger, level="ERROR"):
                result = self.detector.evaluate(snapshot(self.bars))
        self.assertFalse(result["approved"])
        self.assertEqual(result["reason"], "OPPORTUNITY_EVALUATION_ERROR")


class MarketRegimeTests(TestCase):
    def regime_at(self, bars, cut):
        result = OpportunityDetector().evaluate(snapshot(bars[:cut]))
        return result["regime"]["label"], result

    def test_aligned_directional_evidence_can_form_an_early_trend_below_full_adx_threshold(self):
        bars = make_bars(UPTREND)
        config = OpportunityConfig(adx_trending=1000.0)
        parsed = opportunities_module._parse_bars(bars, config)
        indicators = opportunities_module._compute_indicators(parsed, config)
        regime = opportunities_module._classify_regime(
            parsed, indicators, len(parsed.close) - 1, config
        )

        self.assertEqual(regime.label, "EARLY_BULLISH")
        self.assertEqual(regime.ema_bias, "BULL")
        self.assertEqual(regime.structure, "BULLISH")
        self.assertLess(regime.adx, config.adx_trending)

    def test_regime_moves_from_bullish_to_uncertain_to_bearish_on_a_reversal(self):
        bars = make_bars(UPTREND + DOWNTREND)
        before, _ = self.regime_at(bars, 150)
        during, transition = self.regime_at(bars, 170)
        after, _ = self.regime_at(bars, len(bars))

        self.assertEqual((before, during, after), ("BULLISH", "UNCERTAIN", "BEARISH"))
        self.assertEqual(transition["status"], "DEFERRED")
        self.assertEqual(transition["reason"], "MARKET_UNCERTAIN")
        self.assertEqual(transition["regime"]["previous_label"], "BULLISH")
        self.assertTrue(transition["regime"]["regime_changed"])

    def test_no_long_is_approved_once_the_trend_has_reversed(self):
        bars = make_bars(UPTREND + DOWNTREND)
        for cut in range(165, len(bars) + 1):
            result = OpportunityDetector().evaluate(snapshot(bars[:cut]))
            self.assertFalse(result["approved"] and result["side"] == "BUY", f"BUY approved at bar {cut}")

    def test_abrupt_opposite_regime_flip_is_deferred_as_unconfirmed(self):
        real = opportunities_module._classify_regime

        def flipped(bars, indicators, index, config):
            regime = real(bars, indicators, index, config)
            if index < len(bars.close) - 1:
                return replace(regime, label="BEARISH")
            return regime

        with patch.object(opportunities_module, "_classify_regime", side_effect=flipped):
            result = OpportunityDetector().evaluate(snapshot(make_bars(LONG_SETUP)))
        self.assertEqual(result["status"], "DEFERRED")
        self.assertEqual(result["reason"], "REGIME_REVERSAL_UNCONFIRMED")
        self.assertEqual(result["regime"]["previous_label"], "BEARISH")

    def test_uncertain_when_trend_indicators_conflict(self):
        # Rising EMAs but ADX in the grey zone between "ranging" and "trending".
        config = OpportunityConfig(adx_trending=60.0, adx_ranging=10.0)
        result = OpportunityDetector(config=config).evaluate(snapshot(make_bars(LONG_SETUP)))
        self.assertEqual(result["regime"]["label"], "UNCERTAIN")
        self.assertEqual(result["reason"], "MARKET_UNCERTAIN")


class LookAheadTests(TestCase):
    def test_newest_bars_can_never_be_confirmed_pivots(self):
        window = 3
        high = [1, 2, 3, 4, 5, 4, 3, 2, 3, 4, 9]  # newest bar is a spike high
        low = [value - 1 for value in high]
        swing_highs, _ = opportunities_module._pivots(high, low, window)
        self.assertEqual(swing_highs, [4])
        self.assertTrue(all(index <= len(high) - 1 - window for index in swing_highs))

    def test_regime_at_a_bar_is_unchanged_by_later_bars(self):
        config = OpportunityConfig()
        parse = opportunities_module._parse_bars
        compute = opportunities_module._compute_indicators
        classify = opportunities_module._classify_regime
        full_raw = make_bars(UPTREND + DOWNTREND)
        full = parse(full_raw, config)
        full_ind = compute(full, config)
        for t in (130, 150, 170, 200, 240):
            prefix = parse(full_raw[: t + 1], config)
            prefix_ind = compute(prefix, config)
            self.assertEqual(classify(full, full_ind, t, config), classify(prefix, prefix_ind, t, config))

    def test_only_the_newest_max_bars_are_analysed(self):
        config = OpportunityConfig(min_bars=100, max_bars=120)
        bars = make_bars(UPTREND * 2)
        parsed = opportunities_module._parse_bars(bars, config)
        self.assertEqual(len(parsed.close), 120)
        self.assertEqual(parsed.times[-1], bars[-1]["time"])


class ForecastEvidenceTests(TestCase):
    def setUp(self):
        self.bars = make_bars(LONG_SETUP)
        self.bar_time = self.bars[-1]["time"]

    def evaluate(self, detector=None, bars=None):
        detector = detector or OpportunityDetector(use_statistical_forecasts=True)
        return detector.evaluate(snapshot(bars or self.bars))

    def test_agreeing_current_forecasts_confirm_the_setup(self):
        with patch(ARIMA_PATH) as arima, patch(SARIMA_PATH) as sarima:
            arima.return_value = fake_forecast(100.0, 102.0, iso(self.bar_time), rmse=1.0, baseline_rmse=1.2)
            sarima.return_value = fake_forecast(100.0, 101.0, iso(self.bar_time))
            result = self.evaluate()

        self.assertTrue(result["approved"])
        self.assertEqual(result["reason"], "OPPORTUNITY_FOUND_WITH_FORECAST_CONFIRMATION")
        self.assertEqual(result["forecasting"]["arima_quality"], "BEATS_PERSISTENCE")
        self.assertEqual(result["forecasting"]["sarima_quality"], "UNKNOWN")
        self.assertIn("ARIMA/SARIMA agree", result["explanation"])

    def test_conflicting_forecasts_veto_a_valid_setup(self):
        with patch(ARIMA_PATH) as arima, patch(SARIMA_PATH) as sarima:
            arima.return_value = fake_forecast(100.0, 102.0, iso(self.bar_time))
            sarima.return_value = fake_forecast(100.0, 98.0, iso(self.bar_time))
            result = self.evaluate()

        self.assertFalse(result["approved"])
        self.assertEqual(result["status"], "REJECTED")
        self.assertEqual(result["reason"], "FORECAST_DIRECTION_MISMATCH")
        self.assertEqual(result["side"], "HOLD")
        self.assertEqual(result["candidate_side"], "BUY")

    def test_forecasts_oppose_the_trade_direction(self):
        with patch(ARIMA_PATH) as arima, patch(SARIMA_PATH) as sarima:
            arima.return_value = fake_forecast(100.0, 98.0, iso(self.bar_time))
            result = self.evaluate()
            sarima.assert_not_called()  # the expensive model is skipped once the veto is certain
        self.assertEqual(result["reason"], "FORECAST_DIRECTION_MISMATCH")
        self.assertEqual(result["forecasting"]["sarima_direction"], "NOT_EVALUATED")

    def test_forecasts_are_never_computed_for_setups_that_fail_technical_gates(self):
        weak = OpportunityDetector(use_statistical_forecasts=True, minimum_score=0.99)
        ranging = make_bars([(10, 3), (10, -3)] * 10)
        with patch(ARIMA_PATH) as arima, patch(SARIMA_PATH) as sarima:
            self.assertEqual(self.evaluate(weak)["reason"], "SETUP_SCORE_TOO_LOW")
            self.assertEqual(self.evaluate(bars=ranging)["reason"], "MARKET_RANGING")
            arima.assert_not_called()
            sarima.assert_not_called()

    def test_forecasts_are_not_computed_unless_enabled(self):
        with patch(ARIMA_PATH) as arima, patch(SARIMA_PATH) as sarima:
            result = OpportunityDetector().evaluate(snapshot(self.bars))
            arima.assert_not_called()
            sarima.assert_not_called()
        self.assertTrue(result["approved"])
        self.assertNotIn("forecasting", result)

    def test_forecasts_are_cached_per_bar(self):
        detector = OpportunityDetector(use_statistical_forecasts=True)
        with patch(ARIMA_PATH) as arima, patch(SARIMA_PATH) as sarima:
            arima.return_value = fake_forecast(100.0, 102.0, iso(self.bar_time))
            sarima.return_value = fake_forecast(100.0, 101.0, iso(self.bar_time))
            first = self.evaluate(detector)
            second = self.evaluate(detector)
            self.assertEqual(arima.call_count, 1)
            self.assertEqual(sarima.call_count, 1)
        self.assertTrue(first["approved"] and second["approved"])

    def test_forecast_failures_are_cached_and_never_approve(self):
        detector = OpportunityDetector(use_statistical_forecasts=True)
        with patch(ARIMA_PATH, side_effect=ValueError("not enough candles")) as arima, patch(SARIMA_PATH):
            first = self.evaluate(detector)
            second = self.evaluate(detector)
            self.assertEqual(arima.call_count, 1)
        for result in (first, second):
            self.assertFalse(result["approved"])
            self.assertEqual(result["status"], "DEFERRED")
            self.assertEqual(result["reason"], "FORECAST_UNAVAILABLE")

    def test_unexpected_forecast_errors_reject(self):
        with patch(ARIMA_PATH, side_effect=KeyError("boom")), patch(SARIMA_PATH):
            result = self.evaluate()
        self.assertEqual(result["reason"], "FORECAST_ERROR")
        self.assertEqual(result["status"], "REJECTED")

    def test_forecast_built_on_a_different_candle_is_not_trusted(self):
        # e.g. a replay where the database already holds newer candles than the snapshot.
        with patch(ARIMA_PATH) as arima, patch(SARIMA_PATH):
            arima.return_value = fake_forecast(100.0, 102.0, iso(self.bar_time + 3600))
            result = self.evaluate()
        self.assertFalse(result["approved"])
        self.assertEqual(result["reason"], "FORECAST_NOT_CURRENT")
        self.assertEqual(result["status"], "DEFERRED")

    def test_a_vetoed_setup_does_not_consume_the_signal(self):
        detector = OpportunityDetector(use_statistical_forecasts=True)
        with patch(ARIMA_PATH, side_effect=ValueError("db behind")), patch(SARIMA_PATH):
            self.assertFalse(self.evaluate(detector)["approved"])
        # A later bar of the same pullback may still produce the signal.
        later = make_bars(LONG_SETUP + [(1, 1.0)])
        with patch(ARIMA_PATH) as arima, patch(SARIMA_PATH) as sarima:
            when = iso(later[-1]["time"])
            arima.return_value = fake_forecast(100.0, 102.0, when)
            sarima.return_value = fake_forecast(100.0, 101.0, when)
            result = self.evaluate(detector, later)
        self.assertTrue(result["approved"], result["explanation"])


class LegacyContractTests(TestCase):
    def test_legacy_results_gain_status_and_explanation_without_losing_fields(self):
        accepted = OpportunityDetector().evaluate({"trend": "SELL", "setup_score": 0.9})
        self.assertEqual(
            (accepted["approved"], accepted["side"], accepted["score"], accepted["reason"]),
            (True, "SELL", 0.9, "OPPORTUNITY_FOUND"),
        )
        self.assertEqual(accepted["status"], "ACCEPTED")

        for market, reason in (
            ({"trend": "HOLD", "setup_score": 0.9}, "NO_VALID_DIRECTION"),
            ({"trend": "BUY", "setup_score": 1.5}, "INVALID_SETUP_SCORE"),
            ({"trend": "BUY", "setup_score": float("nan")}, "INVALID_SETUP_SCORE"),
            ({"trend": "BUY", "setup_score": 0.1}, "SETUP_SCORE_TOO_LOW"),
        ):
            result = OpportunityDetector().evaluate(market)
            self.assertFalse(result["approved"])
            self.assertEqual(result["reason"], reason)
            self.assertEqual(result["status"], "REJECTED")
            self.assertEqual(result["side"], "HOLD")

    def test_legacy_forecast_veto_keeps_its_reason_codes(self):
        with patch(ARIMA_PATH) as arima, patch(SARIMA_PATH) as sarima:
            arima.return_value = fake_forecast(100.0, 98.0)
            sarima.return_value = fake_forecast(100.0, 98.0)
            result = OpportunityDetector().evaluate(
                {"symbol": "XAUUSD_o", "trend": "BUY", "setup_score": 0.8, "use_statistical_forecasts": True}
            )
        self.assertFalse(result["approved"])
        self.assertEqual(result["reason"], "FORECAST_DIRECTION_MISMATCH")

    def test_bars_take_precedence_over_a_supplied_trend_and_score(self):
        result = OpportunityDetector().evaluate(
            snapshot(make_bars([(10, 3), (10, -3)] * 10), trend="BUY", setup_score=0.99)
        )
        self.assertFalse(result["approved"])
        self.assertEqual(result["reason"], "MARKET_RANGING")

    def test_the_default_decision_endpoint_snapshot_shape_still_works(self):
        outcome = SignalFlowPipeline().run({"trend": "BUY", "setup_score": 0.9, "entry_quality": 0.8, "spread_points": 20})
        self.assertEqual(outcome["stages"]["opportunities"]["side"], "BUY")
        self.assertEqual(outcome["stages"]["entry_timing"]["reason"], "ENTRY_TIMING_APPROVED")


class ConfigurationTests(TestCase):
    def test_invalid_configuration_is_rejected_early(self):
        for kwargs in (
            {"pivot_window": 0},
            {"fast_ema": 50, "slow_ema": 20},
            {"min_bars": 10},
            {"max_bars": 50},
            {"min_retracement": 0.9},
            {"adx_ranging": 30.0, "adx_trending": 20.0},
            {"atr_ratio_min": 3.0},
        ):
            with self.assertRaises(ValueError, msg=str(kwargs)):
                OpportunityConfig(**kwargs)
        with self.assertRaises(ValueError):
            OpportunityDetector(minimum_score=1.5)
        with self.assertRaises(ValueError):
            OpportunityDetector(forecast_limit=10)

    def test_symbol_check_can_be_disabled(self):
        config = OpportunityConfig(symbol_prefixes=None)
        result = OpportunityDetector(config=config).evaluate(
            {"symbol": "EURUSD", "bars": make_bars(LONG_SETUP)}
        )
        self.assertTrue(result["approved"])
