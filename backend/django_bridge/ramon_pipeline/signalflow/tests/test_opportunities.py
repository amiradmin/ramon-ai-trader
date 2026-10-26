from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from ramon_pipeline.signalflow.opportunities import OpportunityDetector


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
