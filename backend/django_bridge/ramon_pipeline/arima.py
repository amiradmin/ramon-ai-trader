"""Backward-compatible import path for ARIMA forecasting.

Implementation lives in ramon_pipeline.signalflow.forecasting.arima.
Keep this shim so existing callers and Django tests continue to work.
"""
from .signalflow.forecasting.arima import (
    TIMEFRAME_MINUTES,
    ArimaForecastResult,
    forecast_market_bars,
)

__all__ = ["TIMEFRAME_MINUTES", "ArimaForecastResult", "forecast_market_bars"]
