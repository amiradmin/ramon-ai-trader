import math
from datetime import datetime, timedelta, timezone

from django.test import TestCase

from ea_bridge.models import MarketBar
from ramon_pipeline.arima import forecast_market_bars


class ArimaForecastTests(TestCase):
    def setUp(self):
        start = datetime(2026, 10, 1, tzinfo=timezone.utc)
        for index in range(60):
            close = 2300.0 + index * 0.07 + math.sin(index / 3) * 0.2
            timestamp = start + timedelta(minutes=15 * index)
            MarketBar.objects.create(
                source="arima-test-ea",
                symbol="XAUUSD_o",
                timeframe="M15",
                timestamp=timestamp,
                open=close - 0.03,
                high=close + 0.15,
                low=close - 0.15,
                close=close,
                volume=1,
            )

    def test_forecast_uses_market_bars_and_returns_holdout_metrics(self):
        result = forecast_market_bars(
            symbol="XAUUSD_o",
            timeframe="M15",
            source="arima-test-ea",
            test_size=5,
        )
        self.assertEqual(result.observations, 60)
        self.assertEqual(result.test_observations, 5)
        self.assertEqual(result.model_order, (1, 1, 1))
        self.assertEqual(result.evaluation_method, "rolling_one_step")
        self.assertTrue(math.isfinite(result.forecast_close))
        self.assertGreaterEqual(result.confidence_upper, result.confidence_lower)
        self.assertGreaterEqual(result.mae, 0)
        self.assertGreaterEqual(result.rmse, 0)
        self.assertGreaterEqual(result.baseline_mae, 0)
        self.assertGreaterEqual(result.baseline_rmse, 0)
        self.assertTrue(math.isfinite(result.baseline_mae))
        self.assertTrue(math.isfinite(result.baseline_rmse))
        self.assertGreaterEqual(result.directional_accuracy, 0)
        self.assertLessEqual(result.directional_accuracy, 1)
        self.assertGreaterEqual(result.convergence_warnings, 0)
        self.assertEqual(result.missing_intervals, 0)
        result_dict = result.as_dict()
        self.assertEqual(result_dict["model_order"], [1, 1, 1])
        self.assertEqual(result_dict["evaluation_method"], "rolling_one_step")
        self.assertIn("baseline_mae", result_dict)
        self.assertIn("baseline_rmse", result_dict)
        self.assertIn("convergence_warnings", result_dict)

    def test_rejects_insufficient_candles(self):
        MarketBar.objects.filter(source="arima-test-ea").delete()
        with self.assertRaisesRegex(ValueError, "not enough candles"):
            forecast_market_bars(symbol="XAUUSD_o", timeframe="M15")

    def test_reports_missing_candle_intervals(self):
        MarketBar.objects.filter(source="arima-test-ea", timestamp__minute=30).delete()
        result = forecast_market_bars(
            symbol="XAUUSD_o",
            timeframe="M15",
            source="arima-test-ea",
            test_size=5,
        )
        self.assertGreater(result.missing_intervals, 0)

    def test_honors_requested_holdout_size(self):
        result = forecast_market_bars(
            symbol="XAUUSD_o",
            timeframe="M15",
            source="arima-test-ea",
            test_size=10,
        )
        self.assertEqual(result.test_observations, 10)
        self.assertEqual(result.train_observations, 50)
