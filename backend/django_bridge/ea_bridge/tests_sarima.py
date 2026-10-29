import math
from datetime import datetime, timedelta, timezone

from django.test import TestCase

from ea_bridge.models import MarketBar
from ramon_pipeline.sarima import forecast_market_bars_sarima


class SarimaForecastTests(TestCase):
    def setUp(self):
        start = datetime(2026, 10, 1, tzinfo=timezone.utc)
        for index in range(70):
            # Synthetic data for software tests only; not market evidence.
            close = (
                2300.0
                + index * 0.04
                + math.sin(index * 2 * math.pi / 5) * 0.3
                + math.sin(index / 3) * 0.05
            )
            MarketBar.objects.create(
                source="sarima-test-ea",
                symbol="XAUUSD_o",
                timeframe="M1",
                timestamp=start + timedelta(minutes=index),
                open=close - 0.03,
                high=close + 0.15,
                low=close - 0.15,
                close=close,
                volume=1,
            )

    def test_returns_rolling_forecast_and_metrics(self):
        result = forecast_market_bars_sarima(
            symbol="XAUUSD_o",
            timeframe="M1",
            source="sarima-test-ea",
            order=(1, 1, 1),
            seasonal_order=(1, 0, 1, 5),
            test_size=5,
        )
        self.assertEqual(result.observations, 70)
        self.assertEqual(result.test_observations, 5)
        self.assertEqual(result.train_observations, 65)
        self.assertEqual(result.evaluation_method, "rolling_one_step")
        self.assertEqual(result.seasonal_order, (1, 0, 1, 5))
        self.assertTrue(math.isfinite(result.forecast_close))
        self.assertGreaterEqual(
            result.confidence_upper, result.confidence_lower
        )
        self.assertGreaterEqual(result.mae, 0)
        self.assertGreaterEqual(result.rmse, 0)
        self.assertGreaterEqual(result.baseline_mae, 0)
        self.assertGreaterEqual(result.baseline_rmse, 0)
        self.assertGreaterEqual(result.directional_accuracy, 0)
        self.assertLessEqual(result.directional_accuracy, 1)
        self.assertGreaterEqual(result.confidence_interval_coverage, 0)
        self.assertLessEqual(result.confidence_interval_coverage, 1)
        self.assertGreaterEqual(result.convergence_warnings, 0)
        self.assertEqual(result.missing_intervals, 0)

        result_dict = result.as_dict()
        self.assertEqual(result_dict["seasonal_order"], [1, 0, 1, 5])
        self.assertIn("confidence_interval_coverage", result_dict)

    def test_rejects_invalid_seasonal_period(self):
        with self.assertRaisesRegex(ValueError, "seasonal period"):
            forecast_market_bars_sarima(
                symbol="XAUUSD_o",
                source="sarima-test-ea",
                seasonal_order=(1, 0, 1, 1),
                test_size=5,
            )

    def test_rejects_insufficient_candles(self):
        MarketBar.objects.filter(source="sarima-test-ea").delete()
        with self.assertRaisesRegex(ValueError, "not enough candles"):
            forecast_market_bars_sarima(
                symbol="XAUUSD_o",
                source="sarima-test-ea",
                test_size=5,
            )
