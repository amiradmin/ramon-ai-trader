"""Backward-compatible import path for SignalFlow's SARIMA forecaster."""
from .signalflow.forecasting.sarima import SarimaForecastResult, forecast_market_bars_sarima

__all__ = ["SarimaForecastResult", "forecast_market_bars_sarima"]
