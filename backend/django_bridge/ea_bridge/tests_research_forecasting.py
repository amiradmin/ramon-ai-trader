import importlib.util
import unittest

from django.test import SimpleTestCase

HAS_SCIKIT_LEARN = importlib.util.find_spec("sklearn") is not None


@unittest.skipUnless(HAS_SCIKIT_LEARN, "install the optional research extra to run ML research tests")
class RegressionForecastingResearchTests(SimpleTestCase):
    def setUp(self):
        import numpy as np

        rng = np.random.default_rng(2026)
        close = 2500.0 * np.exp(np.cumsum(rng.normal(0.0002, 0.001, 240)))
        self.candles = []
        for index, price in enumerate(close):
            previous = close[index - 1] if index else price
            high = max(previous, price) * 1.0005
            low = min(previous, price) * 0.9995
            self.candles.append({
                "open": float(previous),
                "high": float(high),
                "low": float(low),
                "close": float(price),
                "tick_volume": float(100 + index % 17),
            })

    def test_returns_ridge_and_random_forest_reports(self):
        import numpy as np
        from ramon_pipeline.research_forecasting import evaluate_regression_forecasts

        reports = evaluate_regression_forecasts(self.candles, horizon_bars=4)
        self.assertEqual(set(reports), {"ridge", "random_forest"})
        for report in reports.values():
            self.assertEqual(report.evaluation_method, "chronological_holdout")
            self.assertGreater(report.train_observations, report.test_observations)
            self.assertTrue(np.isfinite(report.latest_forecast_return_pct))
            self.assertGreaterEqual(report.directional_accuracy, 0.0)
            self.assertLessEqual(report.directional_accuracy, 1.0)

    def test_rejects_insufficient_candles(self):
        from ramon_pipeline.research_forecasting import evaluate_regression_forecasts

        with self.assertRaisesRegex(ValueError, "at least 50"):
            evaluate_regression_forecasts(self.candles[:40])

    def test_rejects_invalid_ohlc(self):
        from ramon_pipeline.research_forecasting import evaluate_regression_forecasts

        bad = [dict(row) for row in self.candles]
        bad[20]["high"] = bad[20]["low"] * 0.5
        with self.assertRaisesRegex(ValueError, "invalid OHLC"):
            evaluate_regression_forecasts(bad)
