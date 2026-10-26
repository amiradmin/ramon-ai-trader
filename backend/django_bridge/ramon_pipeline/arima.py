"""Backward-compatible import path for SignalFlow's ARIMA forecaster."""
from .signalflow.forecasting.arima import ArimaForecastResult, TIMEFRAME_MINUTES, _interval_for, forecast_market_bars

__all__ = ["ArimaForecastResult", "TIMEFRAME_MINUTES", "forecast_market_bars", "_interval_for"]
