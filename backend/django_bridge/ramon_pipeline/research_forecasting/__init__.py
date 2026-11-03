"""Isolated, research-only forecasting models.

This package is deliberately not imported by the Django decision or risk systems.
Its outputs are diagnostics and must never authorize or create trades.
"""

from .regression import RegressionForecastReport, evaluate_regression_forecasts

__all__ = ["RegressionForecastReport", "evaluate_regression_forecasts"]
