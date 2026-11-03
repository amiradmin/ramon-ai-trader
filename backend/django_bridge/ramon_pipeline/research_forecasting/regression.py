"""Leakage-conscious regression experiments over completed OHLCV candles.

The caller supplies ordered candle mappings. No Django models, database access,
strategy code, risk checks, or order APIs are imported here.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from math import sqrt
from typing import Mapping, Sequence

import numpy as np


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


def _validated_arrays(candles: Sequence[Mapping]):
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

    open_, high, low, close = (column(k) for k in ("open", "high", "low", "close"))
    if any(np.any(v <= 0) for v in (open_, high, low, close)):
        raise ValueError("OHLC prices must be positive")
    if np.any(high < np.maximum(open_, close)) or np.any(low > np.minimum(open_, close)):
        raise ValueError("invalid OHLC candle: high/low does not contain open and close")
    volume = None
    if all("tick_volume" in row for row in candles):
        volume = np.asarray([float(row["tick_volume"]) for row in candles], dtype=float)
        if not np.isfinite(volume).all() or np.any(volume < 0):
            raise ValueError("tick_volume must contain finite non-negative values")
    return open_, high, low, close, volume


def _feature_matrix(open_, high, low, close, volume):
    returns = np.zeros_like(close)
    returns[1:] = np.diff(np.log(close))
    columns = []
    for lag in (1, 2, 4, 8, 16):
        values = np.zeros_like(returns)
        values[lag:] = returns[:-lag]
        columns.append(values)
    for window in (4, 8, 16):
        values = np.zeros_like(returns)
        for index in range(window, len(returns)):
            values[index] = np.std(returns[index-window+1:index+1], ddof=0)
        columns.append(values)
    columns.extend([
        (high-low)/close,
        (close-open_)/open_,
        (2.0*close-high-low)/np.maximum(high-low, 1e-12),
    ])
    if volume is not None:
        changes = np.zeros_like(volume)
        changes[1:] = np.diff(np.log1p(volume))
        lagged = np.zeros_like(changes)
        lagged[4:] = changes[:-4]
        columns.extend([changes, lagged])
    matrix = np.column_stack(columns)
    if not np.isfinite(matrix).all():
        raise ValueError("feature generation produced non-finite values")
    return matrix


def evaluate_regression_forecasts(
    candles: Sequence[Mapping], *, horizon_bars: int = 4,
    test_fraction: float = 0.2, random_state: int = 17,
) -> dict[str, RegressionForecastReport]:
    """Fit Ridge and Random Forest on past data; evaluate a later holdout."""
    if isinstance(horizon_bars, bool) or not isinstance(horizon_bars, int) or not 1 <= horizon_bars <= 32:
        raise ValueError("horizon_bars must be an integer between 1 and 32")
    if not 0.1 <= test_fraction <= 0.4:
        raise ValueError("test_fraction must be between 0.1 and 0.4")
    open_, high, low, close, volume = _validated_arrays(candles)
    features = _feature_matrix(open_, high, low, close, volume)
    indices = np.arange(16, len(close)-horizon_bars)
    if len(indices) < 30:
        raise ValueError("not enough labeled candles after lookback and horizon")
    X = features[indices]
    y = np.log(close[indices+horizon_bars] / close[indices])
    test_start = int(len(indices)*(1.0-test_fraction))
    train_end = test_start-horizon_bars
    if train_end < 25 or len(indices)-test_start < 5:
        raise ValueError("insufficient chronological train/test observations")
    X_train, X_test = X[:train_end], X[test_start:]
    y_train, y_test = y[:train_end], y[test_start:]
    try:
        from sklearn.ensemble import RandomForestRegressor
        from sklearn.linear_model import Ridge
        from sklearn.pipeline import make_pipeline
        from sklearn.preprocessing import StandardScaler
    except ImportError as exc:
        raise RuntimeError(
            "Install the optional research dependencies to run regression "
            "experiments: pip install -e '.[research]'"
        ) from exc
    models = {
        "ridge": make_pipeline(StandardScaler(), Ridge(alpha=10.0)),
        "random_forest": RandomForestRegressor(
            n_estimators=200, max_depth=5, min_samples_leaf=5,
            max_features=0.8, random_state=random_state, n_jobs=-1,
        ),
    }
    baseline = np.zeros_like(y_test)
    baseline_mae = float(np.mean(np.abs(y_test-baseline)))
    baseline_rmse = float(sqrt(np.mean(np.square(y_test-baseline))))
    reports = {}
    for name, estimator in models.items():
        estimator.fit(X_train, y_train)
        prediction = np.asarray(estimator.predict(X_test), dtype=float)
        errors = y_test-prediction
        latest = float(estimator.predict(features[[-1]])[0])
        mae = float(np.mean(np.abs(errors)))
        rmse = float(sqrt(np.mean(np.square(errors))))
        reports[name] = RegressionForecastReport(
            model=name, horizon_bars=horizon_bars, observations=len(indices),
            train_observations=len(X_train), test_observations=len(X_test),
            evaluation_method="chronological_holdout", mae=mae, rmse=rmse,
            directional_accuracy=float(np.mean(np.sign(y_test)==np.sign(prediction))),
            baseline_mae=baseline_mae, baseline_rmse=baseline_rmse,
            beats_zero_return_baseline=mae<baseline_mae and rmse<baseline_rmse,
            latest_forecast_log_return=latest,
            latest_forecast_return_pct=float((np.exp(latest)-1.0)*100.0),
        )
    return reports
