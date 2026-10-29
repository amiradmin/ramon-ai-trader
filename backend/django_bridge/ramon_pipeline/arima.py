"""Backward-compatible import for the ARIMA forecast utility.

The implementation lives in SignalFlow's forecasting package. Keep this module
so existing Django commands, tests, and integrations can continue importing
`ramon_pipeline.arima`.
"""
from .signalflow.forecasting.arima import ArimaForecastResult, forecast_market_bars

__all__ = ["ArimaForecastResult", "forecast_market_bars"]
