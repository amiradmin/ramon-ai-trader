from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from ramon_pipeline.signalflow.opportunities import OpportunityDetector


def make_candles(closes):
    candles = []
    for index, close in enumerate(closes):
        open_price = close - 0.2
        if index == len(closes) - 1 and len(closes) >= 4:
            open_price = closes[-2] - 0.3
        candles.append({
            "timestamp": f"bar-{index:04d}",
            "open": open_price,
            "high": max(open_price, close) + 0.4,
            "low": min(open_price, close) - 0.4,
            "close": close,
            "is_closed": True,
        })
    return candles


def bullish_pullback_candles():
    closes = [2000.0 + index * 0.5 for index in range(50)]
    closes[-4] = closes[-5] - 1.0
    closes[-3] = closes[-5] - 2.0
    closes[-2] = closes[-5] - 1.0
    closes[-1] = closes[-2] + 2.0
    return make_candles(closes)


class OpportunityDetectorTests(TestCase):
    def test_legacy_market_signal_remains_supported_by_default(self):
        result = OpportunityDetector().evaluate(
            {"trend": "BUY", "setup_score": 0.8}
        )
        self.assertTrue(result["approved"])
        self.assertEqual(result["side"], "BUY")
        self.assertEqual(result["reason"], "OPPORTUNITY_FOUND")

    def test_candle_strategy_accepts_a_confirmed_bullish_pullback(self):
        result = OpportunityDetector().evaluate({
            "symbol": "XAUUSD_o",
            "timeframe": "M15",
            "candles": bullish_pullback_candles(),
        })
        self.assertTrue(result["approved"], result)
        self.assertEqual(result["side"], "BUY")
        self.assertEqual(result["market_regime"], "BULLISH")
        self.assertTrue(result["evidence"]["pullback_seen"])
        self.assertTrue(result["evidence"]["continuation_candle"])
        self.assertTrue(result["signal_key"])
        self.assertIn("trend", result["score_components"])

    def test_ranging_market_does_not_produce_an_opportunity(self):
        closes = [2000.0 + (index % 2) * 0.02 for index in range(50)]
        result = OpportunityDetector().evaluate({"candles": make_candles(closes)})
        self.assertFalse(result["approved"])
        self.assertEqual(result["side"], "HOLD")
        self.assertEqual(result["market_regime"], "RANGING")
        self.assertEqual(result["reason"], "NO_TREND_PULLBACK_SETUP")

    def test_missing_or_insufficient_candles_fail_closed(self):
        detector = OpportunityDetector()
        missing = detector.evaluate({"symbol": "XAUUSD_o", "candles": []})
        malformed = detector.evaluate({"candles": [{"open": 1, "high": 2}]})
        self.assertFalse(missing["approved"])
        self.assertEqual(missing["reason"], "INSUFFICIENT_CANDLE_HISTORY")
        self.assertFalse(malformed["approved"])
        self.assertEqual(malformed["reason"], "INSUFFICIENT_CANDLE_HISTORY")

    def test_non_chronological_candles_are_rejected(self):
        candles = bullish_pullback_candles()
        candles[-1]["timestamp"] = candles[-2]["timestamp"]
        result = OpportunityDetector().evaluate({"candles": candles})
        self.assertFalse(result["approved"])
        self.assertEqual(result["reason"], "CANDLE_TIMESTAMPS_NOT_STRICTLY_INCREASING")

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
        self.assertEqual(result["reason"], "FORECAST_DIRECTION_MISMATCH")

    def test_forecasts_are_not_computed_for_rejected_setups(self):
        with patch.object(OpportunityDetector, "_evaluate_forecasts") as forecasts:
            result = OpportunityDetector().evaluate({
                "trend": "BUY",
                "setup_score": 0.2,
                "use_statistical_forecasts": True,
            })
        self.assertFalse(result["approved"])
        forecasts.assert_not_called()
