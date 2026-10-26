"""ARIMA forecasting utilities for completed EA candles stored in MarketBar.

This is a research/forecasting component only. It does not authorize or place trades.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

import numpy as np
from statsmodels.tsa.arima.model import ARIMA

from ea_bridge.models import MarketBar


TIMEFRAME_MINUTES = {
    "M1": 1,
    "M2": 2,
    "M3": 3,
    "M4": 4,
    "M5": 5,
    "M6": 6,
    "M10": 10,
    "M12": 12,
    "M15": 15,
    "M20": 20,
    "M30": 30,
    "H1": 60,
    "H2": 120,
    "H3": 180,
    "H4": 240,
    "H6": 360,
    "H8": 480,
    "H12": 720,
    "D1": 1440,
    "W1": 10080,
}


@dataclass(frozen=True)
class ArimaForecastResult:
    symbol: str
    timeframe: str
    model_order: tuple[int, int, int]
    observations: int
    train_observations: int
    test_observations: int
    last_bar_time: str
    target_time: str
    last_close: float
    forecast_close: float
    confidence_lower: float
    confidence_upper: float
    mae: float
    rmse: float
    directional_accuracy: float
    missing_intervals: int

    def as_dict(self) -> dict:
        result = dict(self.__dict__)
        result["model_order"] = list(self.model_order)
        return result


def _interval_for(timeframe: str) -> timedelta:
    key = timeframe.upper()
    if key not in TIMEFRAME_MINUTES:
        raise ValueError(f"unsupported timeframe {timeframe!r}")
    return timedelta(minutes=TIMEFRAME_MINUTES[key])


def forecast_market_bars(
    *,
    symbol: str,
    timeframe: str = "M15",
    source: str | None = None,
    limit: int = 1000,
    order: tuple[int, int, int] = (1, 1, 1),
    test_size: int = 20,
) -> ArimaForecastResult:
    """Fit ARIMA on ordered candle closes, evaluate a chronological holdout, forecast one bar."""
    if limit < 40:
        raise ValueError("limit must be at least 40 candles")
    if test_size < 1:
        raise ValueError("test_size must be positive")
    _interval_for(timeframe)

    query = MarketBar.objects.filter(symbol=symbol, timeframe=timeframe.upper())
    if source:
        query = query.filter(source=source)
    bars = list(query.order_by("-timestamp")[:limit])
    bars.reverse()
    if len(bars) < max(40, test_size + 20):
        raise ValueError(
            f"not enough candles: found {len(bars)}, need at least {max(40, test_size + 20)}"
        )

    closes = np.asarray([float(bar.close) for bar in bars], dtype=float)
    if not np.isfinite(closes).all():
        raise ValueError("candle closes must all be finite numbers")
    if np.any(closes <= 0):
        raise ValueError("candle closes must be positive")
    if not np.all(np.diff([bar.timestamp.timestamp() for bar in bars]) > 0):
        raise ValueError("candle timestamps must be strictly increasing")

    interval = _interval_for(timeframe)
    missing_intervals = 0
    for previous, current in zip(bars, bars[1:]):
        elapsed = (current.timestamp - previous.timestamp).total_seconds()
        expected = interval.total_seconds()
        if elapsed > expected * 1.5:
            missing_intervals += max(1, round(elapsed / expected) - 1)

    split = len(closes) - min(test_size, max(1, len(closes) // 4))
    train, test = closes[:split], closes[split:]
    fitted = ARIMA(train, order=order, enforce_stationarity=False, enforce_invertibility=False).fit()
    test_forecast = np.asarray(fitted.forecast(steps=len(test)), dtype=float)
    errors = test - test_forecast
    mae = float(np.mean(np.abs(errors)))
    rmse = float(np.sqrt(np.mean(np.square(errors))))

    # Direction is measured against the preceding observed close, not price direction in isolation.
    actual_direction = np.sign(test - closes[split - 1 : -1])
    predicted_direction = np.sign(test_forecast - closes[split - 1 : -1])
    directional_accuracy = float(np.mean(actual_direction == predicted_direction))

    final_fit = ARIMA(closes, order=order, enforce_stationarity=False, enforce_invertibility=False).fit()
    prediction = final_fit.get_forecast(steps=1)
    forecast_close = float(np.asarray(prediction.predicted_mean).reshape(-1)[0])
    interval_frame = prediction.conf_int(alpha=0.05)
    lower, upper = float(interval_frame[0][0]), float(interval_frame[0][1])
    last_bar = bars[-1]
    return ArimaForecastResult(
        symbol=symbol,
        timeframe=timeframe.upper(),
        model_order=order,
        observations=len(closes),
        train_observations=len(train),
        test_observations=len(test),
        last_bar_time=last_bar.timestamp.isoformat(),
        target_time=(last_bar.timestamp + interval).isoformat(),
        last_close=float(closes[-1]),
        forecast_close=forecast_close,
        confidence_lower=lower,
        confidence_upper=upper,
        mae=mae,
        rmse=rmse,
        directional_accuracy=directional_accuracy,
        missing_intervals=missing_intervals,
    )
