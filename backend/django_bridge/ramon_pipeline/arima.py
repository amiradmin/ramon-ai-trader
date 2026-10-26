"""ARIMA forecasting utilities for completed EA candles stored in MarketBar.

This is a research/forecasting component only. It does not authorize or place trades.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import warnings

import numpy as np
from statsmodels.tools.sm_exceptions import ConvergenceWarning
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
    evaluation_method: str
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
    baseline_mae: float
    baseline_rmse: float
    directional_accuracy: float
    convergence_warnings: int
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
    """Evaluate ARIMA with rolling one-step forecasts, then forecast the next bar.

    Each holdout forecast is fitted only on candles that precede that forecast,
    preventing future holdout closes from leaking into earlier predictions.
    A persistence baseline predicts that the next close equals the latest known
    close. Missing time intervals are counted and reported; bars are not
    fabricated to fill market closures or data gaps.
    """
    if limit < 40:
        raise ValueError("limit must be at least 40 candles")
    if test_size < 1:
        raise ValueError("test_size must be positive")
    if len(order) != 3 or any(not isinstance(value, int) or value < 0 for value in order):
        raise ValueError("ARIMA order must contain three non-negative integers")
    interval = _interval_for(timeframe)

    query = MarketBar.objects.filter(symbol=symbol, timeframe=timeframe.upper())
    if source:
        query = query.filter(source=source)
    bars = list(query.order_by("-timestamp")[:limit])
    bars.reverse()
    minimum = max(40, test_size + 20)
    if len(bars) < minimum:
        raise ValueError(f"not enough candles: found {len(bars)}, need at least {minimum}")

    closes = np.asarray([float(bar.close) for bar in bars], dtype=float)
    if not np.isfinite(closes).all():
        raise ValueError("candle closes must all be finite numbers")
    if np.any(closes <= 0):
        raise ValueError("candle closes must be positive")

    timestamps = [bar.timestamp.timestamp() for bar in bars]
    if not np.all(np.diff(timestamps) > 0):
        raise ValueError("candle timestamps must be strictly increasing")

    missing_intervals = 0
    expected_seconds = interval.total_seconds()
    for previous, current in zip(bars, bars[1:]):
        elapsed = (current.timestamp - previous.timestamp).total_seconds()
        if elapsed > expected_seconds * 1.5:
            missing_intervals += max(1, round(elapsed / expected_seconds) - 1)

    # Honor the requested holdout size exactly. Each forecast uses only data
    # available before its target candle (expanding-window walk-forward test).
    split = len(closes) - test_size
    train = closes[:split]
    test = closes[split:]
    rolling_forecasts = []
    convergence_warning_count = 0
    for target_index in range(split, len(closes)):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always", ConvergenceWarning)
            fitted = ARIMA(
                closes[:target_index],
                order=order,
                enforce_stationarity=False,
                enforce_invertibility=False,
            ).fit()
            forecast = np.asarray(
                fitted.forecast(steps=1), dtype=float
            ).reshape(-1)
        convergence_warning_count += sum(
            issubclass(item.category, ConvergenceWarning) for item in caught
        )
        rolling_forecasts.append(float(forecast[0]))

    test_forecast = np.asarray(rolling_forecasts, dtype=float)
    errors = test - test_forecast
    mae = float(np.mean(np.abs(errors)))
    rmse = float(np.sqrt(np.mean(np.square(errors))))

    # Persistence baseline: predict no change from the most recently observed close.
    baseline_forecast = closes[split - 1 : -1]
    baseline_errors = test - baseline_forecast
    baseline_mae = float(np.mean(np.abs(baseline_errors)))
    baseline_rmse = float(np.sqrt(np.mean(np.square(baseline_errors))))

    actual_direction = np.sign(test - closes[split - 1 : -1])
    predicted_direction = np.sign(test_forecast - closes[split - 1 : -1])
    directional_accuracy = float(np.mean(actual_direction == predicted_direction))

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", ConvergenceWarning)
        final_fit = ARIMA(
            closes,
            order=order,
            enforce_stationarity=False,
            enforce_invertibility=False,
        ).fit()
        prediction = final_fit.get_forecast(steps=1)
    convergence_warning_count += sum(
        issubclass(item.category, ConvergenceWarning) for item in caught
    )
    forecast_close = float(np.asarray(prediction.predicted_mean).reshape(-1)[0])
    interval_frame = prediction.conf_int(alpha=0.05)
    lower, upper = float(interval_frame[0][0]), float(interval_frame[0][1])
    last_bar = bars[-1]
    return ArimaForecastResult(
        symbol=symbol,
        timeframe=timeframe.upper(),
        model_order=order,
        evaluation_method="rolling_one_step",
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
        baseline_mae=baseline_mae,
        baseline_rmse=baseline_rmse,
        directional_accuracy=directional_accuracy,
        convergence_warnings=convergence_warning_count,
        missing_intervals=missing_intervals,
    )
