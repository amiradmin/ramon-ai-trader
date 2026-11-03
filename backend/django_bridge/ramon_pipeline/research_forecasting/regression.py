"""Leakage-conscious regression experiments over completed OHLCV candles.

The caller supplies an ordered sequence of candle mappings. No Django models,
database access, strategy code, risk checks, or order APIs are imported here.
The target is the forward log return over a configurable horizon.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from math import sqrt
from typing import Mapping, Sequence

import numpy as np
from sklearn.ensemble import RandomForestRegressor
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler


@dataclass(frozen=True)
class RegressionForecastReport:
    model: str
    horizon_bars: int
    observations: int
    train_observations: int
    test_observations: int
    evaluation_method: str
    mae: float
    rmse: float
    directional_accuracy: float
    baseline_mae: float
    baseline_rmse: float
    beats_zero_return_baseline: bool
    latest_forecast_log_return: float
    latest_forecast_return_pct: float

    def as_dict(self) -> dict:
        return asdict(self)


def _validated_arrays(candles: Sequence[Mapping]) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray | None]:
    if len(candles) < 50:
        raise ValueError("at least 50 completed candles are required")

    def column(name: str) -> np.ndarray:
        try:
            values = np.asarray([float(row[name]) for row in candles], dtype=float)
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"every candle must contain a valid {name!r}") from exc
        if not np.isfinite(values).all():
            raise ValueError(f"candle field {name!r} must contain finite values")
        return values

    open_ = column("open")
    high = column("high")
    low = column("low")
    close = column("close")
    if np.any(open_ <= 0) or np.any(high <= 0) or np.any(low <= 0) or np.any(close <= 0):
        raise ValueError("OHLC prices must be positive")
    if np.any(high < np.maximum(open_, close)) or np.any(low > np.minimum(open_, close)):
        raise ValueError("invalid OHLC candle: high/low does not contain open and close")

    volume = None
    if all("tick_volume" in row for row in candles):
        volume = np.asarray([float(row["tick_volume"]) for row in candles], dtype=float)
        if not np.isfinite(volume).all() or np.any(volume < 0):
            raise ValueError("tick_volume must contain finite non-negative values")
    return open_, high, low, close, volume


def _feature_matrix(open_: np.ndarray, high: np.ndarray, low: np.ndarray, close: np.ndarray, volume: np.ndarray | None) -> tuple[np.ndarray, list[str]]:
    returns = np.zeros_like(close)
    returns[1:] = np.diff(np.log(close))
    names = ["return_lag_1", "return_lag_2", "return_lag_4", "return_lag_8", "return_lag_16",
             "volatility_4", "volatility_8", "volatility_16", "range_pct", "body_pct", "close_location"]
    columns = []
    for lag in (1, 2, 4, 8, 16):
        lagged = np.zeros_like(returns)
        lagged[lag:] = returns[:-lag]
        columns.append(lagged)
    for window in (4, 8, 16):
        values = np.zeros_like(returns)
        for index in range(window, len(returns)):
            values[index] = float(np.std(returns[index - window + 1:index + 1], ddof=0))
        columns.append(values)
    columns.extend([
        (high - low) / close,
        (close - open_) / open_,
        (2.0 * close - high - low) / np.maximum(high - low, 1e-12),
    ])
    if volume is not None:
        names.extend(["volume_change_1", "volume_change_4"])
        volume_change = np.zeros_like(volume)
        volume_change[1:] = np.diff(np.log1p(volume))
        volume_lag_4 = np.zeros_like(volume_change)
        volume_lag_4[4:] = volume_change[:-4]
        columns.extend([volume_change, volume_lag_4])
    matrix = np.column_stack(columns)
    if not np.isfinite(matrix).all():
        raise ValueError("feature generation produced non-finite values")
    return matrix, names


def evaluate_regression_forecasts(
    candles: Sequence[Mapping],
    *,
    horizon_bars: int = 4,
    test_fraction: float = 0.2,
    random_state: int = 17,
) -> dict[str, RegressionForecastReport]:
    """Fit Ridge and Random Forest on past data; evaluate on a later holdout.

    Features for candle t use information no later than candle t. The target is
    log(close[t+horizon] / close[t]). The final holdout is chronological and is
    never shuffled. The zero-return baseline predicts no price change.
    """
    if isinstance(horizon_bars, bool) or not isinstance(horizon_bars, int) or horizon_bars < 1 or horizon_bars > 32:
        raise ValueError("horizon_bars must be an integer between 1 and 32")
    if not 0.1 <= test_fraction <= 0.4:
        raise ValueError("test_fraction must be between 0.1 and 0.4")

    open_, high, low, close, volume = _validated_arrays(candles)
    features, _ = _feature_matrix(open_, high, low, close, volume)
    last_target_index = len(close) - horizon_bars
    # Only rows with a fully observed forward target enter model evaluation.
    indices = np.arange(16, last_target_index)
    if len(indices) < 30:
        raise ValueError("not enough labeled candles after lookback and horizon")
    X = features[indices]
    y = np.log(close[indices + horizon_bars] / close[indices])
    test_start = int(len(indices) * (1.0 - test_fraction))
    # Purge training rows whose forward target window overlaps the test period.
    train_end = test_start - horizon_bars
    if train_end < 25 or len(indices) - test_start < 5:
        raise ValueError("insufficient chronological train/test observations")
    X_train, X_test = X[:train_end], X[test_start:]
    y_train, y_test = y[:train_end], y[test_start:]

    models = {
        "ridge": make_pipeline(StandardScaler(), Ridge(alpha=10.0)),
        "random_forest": RandomForestRegressor(
            n_estimators=200, max_depth=5, min_samples_leaf=5,
            max_features=0.8, random_state=random_state, n_jobs=-1,
        ),
    }
    latest_features = features[[-1]]
    baseline = np.zeros_like(y_test)
    baseline_mae = float(np.mean(np.abs(y_test - baseline)))
    baseline_rmse = float(sqrt(np.mean(np.square(y_test - baseline))))
    reports = {}
    for name, estimator in models.items():
        estimator.fit(X_train, y_train)
        prediction = np.asarray(estimator.predict(X_test), dtype=float)
        errors = y_test - prediction
        mae = float(np.mean(np.abs(errors)))
        rmse = float(sqrt(np.mean(np.square(errors))))
        directional_accuracy = float(np.mean(np.sign(y_test) == np.sign(prediction)))
        latest_prediction = float(estimator.predict(latest_features)[0])
        reports[name] = RegressionForecastReport(
            model=name,
            horizon_bars=horizon_bars,
            observations=len(indices),
            train_observations=len(X_train),
            test_observations=len(X_test),
            evaluation_method="chronological_holdout",
            mae=mae,
            rmse=rmse,
            directional_accuracy=directional_accuracy,
            baseline_mae=baseline_mae,
            baseline_rmse=baseline_rmse,
            beats_zero_return_baseline=mae < baseline_mae and rmse < baseline_rmse,
            latest_forecast_log_return=latest_prediction,
            latest_forecast_return_pct=float((np.exp(latest_prediction) - 1.0) * 100.0),
        )
    return reports
