"""Research-only SARIMA forecasts for completed MarketBar candles.

This module does not authorize or place trades. Seasonal periods must be
selected and validated experimentally; no market periodicity is assumed.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import warnings

import numpy as np
from statsmodels.tools.sm_exceptions import ConvergenceWarning
from statsmodels.tsa.statespace.sarimax import SARIMAX

from ea_bridge.models import MarketBar
from .arima import TIMEFRAME_MINUTES, _interval_for


@dataclass(frozen=True)
class SarimaForecastResult:
    symbol: str
    timeframe: str
    model_order: tuple[int, int, int]
    seasonal_order: tuple[int, int, int, int]
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
    confidence_interval_coverage: float
    convergence_warnings: int
    missing_intervals: int

    def as_dict(self) -> dict:
        result = dict(self.__dict__)
        result["model_order"] = list(self.model_order)
        result["seasonal_order"] = list(self.seasonal_order)
        return result


def forecast_market_bars_sarima(
    *,
    symbol: str,
    timeframe: str = "M1",
    source: str | None = None,
    limit: int = 1000,
    order: tuple[int, int, int] = (1, 1, 1),
    seasonal_order: tuple[int, int, int, int] = (1, 0, 1, 5),
    test_size: int = 10,
) -> SarimaForecastResult:
    """Evaluate seasonal ARIMA with rolling one-step forecasts.

    The seasonal period is explicit and configurable. The default period of
    five bars is only an initial experiment, not a claim about market seasonality.
    Each holdout fit uses only observations available before its target candle.
    """
    if limit < 40:
        raise ValueError("limit must be at least 40 candles")
    if test_size < 1:
        raise ValueError("test_size must be positive")
    if len(order) != 3 or any(
        not isinstance(v, int) or isinstance(v, bool) or v < 0 for v in order
    ):
        raise ValueError("ARIMA order must contain three non-negative integers")
    if len(seasonal_order) != 4 or any(
        not isinstance(v, int) or isinstance(v, bool) or v < 0
        for v in seasonal_order
    ):
        raise ValueError(
            "seasonal order must contain four non-negative integers (P,D,Q,s)"
        )

    seasonal_period = seasonal_order[3]
    if seasonal_period < 2:
        raise ValueError("seasonal period s must be at least 2")

    interval = _interval_for(timeframe)
    query = MarketBar.objects.filter(
        symbol=symbol, timeframe=timeframe.upper()
    )
    if source:
        query = query.filter(source=source)

    bars = list(query.order_by("-timestamp")[:limit])
    bars.reverse()

    minimum = max(40, test_size + 20, 3 * seasonal_period + test_size)
    if len(bars) < minimum:
        raise ValueError(
            f"not enough candles: found {len(bars)}, need at least {minimum}"
        )

    closes = np.asarray([float(bar.close) for bar in bars], dtype=float)
    if not np.isfinite(closes).all():
        raise ValueError("candle closes must all be finite numbers")
    if np.any(closes <= 0):
        raise ValueError("candle closes must be positive")

    timestamps = np.asarray(
        [bar.timestamp.timestamp() for bar in bars], dtype=float
    )
    if not np.all(np.diff(timestamps) > 0):
        raise ValueError("candle timestamps must be strictly increasing")

    expected_seconds = interval.total_seconds()
    missing_intervals = 0
    for previous, current in zip(bars, bars[1:]):
        elapsed = (current.timestamp - previous.timestamp).total_seconds()
        if elapsed > expected_seconds * 1.5:
            missing_intervals += max(
                1, round(elapsed / expected_seconds) - 1
            )

    split = len(closes) - test_size
    forecasts: list[float] = []
    lowers: list[float] = []
    uppers: list[float] = []
    convergence_warning_count = 0

    for target_index in range(split, len(closes)):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always", ConvergenceWarning)
            fitted = SARIMAX(
                closes[:target_index],
                order=order,
                seasonal_order=seasonal_order,
                enforce_stationarity=False,
                enforce_invertibility=False,
            ).fit(disp=False)

            prediction = fitted.get_forecast(steps=1)
            forecast = np.asarray(
                prediction.predicted_mean, dtype=float
            ).reshape(-1)
            bounds = np.asarray(prediction.conf_int(alpha=0.05), dtype=float)

        convergence_warning_count += sum(
            issubclass(item.category, ConvergenceWarning) for item in caught
        )
        forecasts.append(float(forecast[0]))
        lowers.append(float(bounds[0, 0]))
        uppers.append(float(bounds[0, 1]))

    test = closes[split:]
    test_forecast = np.asarray(forecasts)
    errors = test - test_forecast
    mae = float(np.mean(np.abs(errors)))
    rmse = float(np.sqrt(np.mean(np.square(errors))))

    baseline_forecast = closes[split - 1:-1]
    baseline_errors = test - baseline_forecast
    baseline_mae = float(np.mean(np.abs(baseline_errors)))
    baseline_rmse = float(np.sqrt(np.mean(np.square(baseline_errors))))

    previous_closes = closes[split - 1:-1]
    actual_direction = np.sign(test - previous_closes)
    predicted_direction = np.sign(test_forecast - previous_closes)
    directional_accuracy = float(
        np.mean(actual_direction == predicted_direction)
    )
    interval_coverage = float(
        np.mean(
            (test >= np.asarray(lowers)) &
            (test <= np.asarray(uppers))
        )
    )

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", ConvergenceWarning)
        final_fit = SARIMAX(
            closes,
            order=order,
            seasonal_order=seasonal_order,
            enforce_stationarity=False,
            enforce_invertibility=False,
        ).fit(disp=False)
        prediction = final_fit.get_forecast(steps=1)

    convergence_warning_count += sum(
        issubclass(item.category, ConvergenceWarning) for item in caught
    )
    forecast_close = float(
        np.asarray(prediction.predicted_mean, dtype=float).reshape(-1)[0]
    )
    bounds = np.asarray(prediction.conf_int(alpha=0.05), dtype=float)
    last_bar = bars[-1]

    return SarimaForecastResult(
        symbol=symbol,
        timeframe=timeframe.upper(),
        model_order=order,
        seasonal_order=seasonal_order,
        evaluation_method="rolling_one_step",
        observations=len(closes),
        train_observations=split,
        test_observations=len(test),
        last_bar_time=last_bar.timestamp.isoformat(),
        target_time=(last_bar.timestamp + interval).isoformat(),
        last_close=float(closes[-1]),
        forecast_close=forecast_close,
        confidence_lower=float(bounds[0, 0]),
        confidence_upper=float(bounds[0, 1]),
        mae=mae,
        rmse=rmse,
        baseline_mae=baseline_mae,
        baseline_rmse=baseline_rmse,
        directional_accuracy=directional_accuracy,
        confidence_interval_coverage=interval_coverage,
        convergence_warnings=convergence_warning_count,
        missing_intervals=missing_intervals,
    )
