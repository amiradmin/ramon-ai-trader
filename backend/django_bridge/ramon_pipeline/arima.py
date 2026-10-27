"""Backward-compatible ARIMA imports."""
from .signalflow.forecasting.arima import (
    ArimaForecastResult,
    forecast_market_bars,
)

__all__ = ["ArimaForecastResult", "forecast_market_bars"]
