"""Independent, leak-safe regression pipelines for trade-outcome learning.

The fitted estimators are research artifacts only. They are never loaded by the
live decision or risk path.
"""
from __future__ import annotations

import base64
import hashlib
import io
import math
from typing import Any


from ea_bridge.models import LearningTradeOutcome


PIPELINES: dict[str, dict[str, str]] = {
    "trade_performance": {
        "name": "Performance · Random Forest",
        "target": "r_multiple",
        "estimator": "random_forest",
        "description": "Expected R-multiple from pre-trade context.",
    },
    "trade_decision": {
        "name": "Decision · HistGradientBoosting",
        "target": "r_multiple",
        "estimator": "hist_gradient_boosting",
        "description": "Expected R-multiple for explicitly linked decisions.",
    },
    "trade_entry_exit": {
        "name": "Entry/Exit · Extra Trees",
        "target": "slippage_points",
        "estimator": "extra_trees",
        "description": "Observed slippage from pre-trade context; requires broker slippage labels.",
    },
    "trade_regime": {
        "name": "Regime · HistGradientBoosting",
        "target": "r_multiple",
        "estimator": "hist_gradient_boosting",
        "description": "Expected R-multiple for a recorded market regime.",
    },
    "trade_mistake": {
        "name": "Mistake · Huber Regression",
        "target": "loss_severity_r",
        "estimator": "huber",
        "description": "Non-negative loss severity, max(0, -R), from pre-trade context.",
    },
    "trade_strategy": {
        "name": "Strategy · Random Forest",
        "target": "r_multiple",
        "estimator": "random_forest",
        "description": "Expected R-multiple for a recorded strategy profile.",
    },
}

# Only fields known before the order is opened are admitted. In particular,
# realized PnL, R, MAE/MFE, exit metrics, and outcome-derived analyzer results
# are intentionally excluded from every feature vector.
_TAG_FEATURES = (
    "side", "regime", "setup_type", "score", "forecast_confirmed",
    "strategy_profile", "entry_timing",
)
_SNAPSHOT_NUMERIC_FEATURES = (
    "confidence", "adx", "rsi", "atr", "spread_points",
    "volatility", "ema_distance", "trend_strength",
)
_MIN_TRAINING_ROWS = 40
_MIN_HOLDOUT_ROWS = 8


def _feature_vector(row: LearningTradeOutcome) -> dict[str, float | str]:
    tags = row.tags or {}
    snapshot = row.decision_snapshot or {}
    features: dict[str, float | str] = {}
    for key in _TAG_FEATURES:
        value = tags.get(key)
        if value is None:
            value = snapshot.get(key)
        if isinstance(value, bool):
            features[f"tag_{key}"] = "true" if value else "false"
        elif isinstance(value, (str, int, float)) and not isinstance(value, bool):
            if isinstance(value, float) and not math.isfinite(value):
                continue
            features[f"tag_{key}"] = str(value) if isinstance(value, str) else float(value)
    for key in _SNAPSHOT_NUMERIC_FEATURES:
        value = snapshot.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value)):
            features[f"snapshot_{key}"] = float(value)
    return features


def _target(row: LearningTradeOutcome, target_name: str) -> float | None:
    if target_name == "r_multiple":
        return float(row.r_multiple) if row.r_multiple is not None else None
    if target_name == "loss_severity_r":
        return max(0.0, -float(row.r_multiple)) if row.r_multiple is not None else None
    value = (row.execution_metrics or {}).get(target_name)
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value)):
        return float(value)
    return None


def _estimator(name: str) -> Any:
    from sklearn.ensemble import ExtraTreesRegressor, HistGradientBoostingRegressor, RandomForestRegressor
    from sklearn.linear_model import HuberRegressor
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    if name == "random_forest":
        return RandomForestRegressor(
            n_estimators=160, min_samples_leaf=3, max_features=0.8,
            random_state=42, n_jobs=1,
        )
    if name == "hist_gradient_boosting":
        return HistGradientBoostingRegressor(
            max_iter=120, max_leaf_nodes=15, l2_regularization=1.0,
            random_state=42,
        )
    if name == "extra_trees":
        return ExtraTreesRegressor(
            n_estimators=160, min_samples_leaf=3, max_features=0.8,
            random_state=42, n_jobs=1,
        )
    if name == "huber":
        return make_pipeline(StandardScaler(), HuberRegressor(max_iter=500, epsilon=1.5))
    raise ValueError(f"Unknown regression estimator: {name}")


def train_trade_outcome_pipeline(model_key: str) -> dict[str, Any]:
    """Fit one component-specific regressor and evaluate on the latest chronological holdout."""
    from sklearn.feature_extraction import DictVectorizer
    from sklearn.metrics import mean_absolute_error, mean_squared_error
    import joblib

    spec = PIPELINES.get(model_key)
    if spec is None:
        raise ValueError(f"Unknown trade-outcome pipeline: {model_key}")

    query = LearningTradeOutcome.objects.filter(closed_at__isnull=False).order_by("closed_at", "id")
    if model_key == "trade_decision":
        query = query.exclude(decision_id="")
    elif model_key == "trade_regime":
        query = query.exclude(tags__regime__isnull=True).exclude(tags__regime="")
    elif model_key == "trade_strategy":
        query = query.exclude(tags__strategy_profile__isnull=True).exclude(tags__strategy_profile="")

    samples: list[tuple[Any, dict[str, float | str], float]] = []
    for row in query.iterator():
        target = _target(row, spec["target"])
        features = _feature_vector(row)
        if target is None or not features:
            continue
        if model_key == "trade_decision" and not row.decision_snapshot:
            # A decision id without a captured pre-trade feature snapshot is
            # attributable but not a valid supervised feature row.
            continue
        if model_key == "trade_regime" and not (row.tags or {}).get("regime"):
            continue
        if model_key == "trade_strategy" and not (row.tags or {}).get("strategy_profile"):
            continue
        if model_key == "trade_entry_exit" and (row.execution_metrics or {}).get("slippage_points") is None:
            continue
        samples.append((row.closed_at, features, float(target)))

    if len(samples) < _MIN_TRAINING_ROWS + _MIN_HOLDOUT_ROWS:
        raise ValueError(
            f"Insufficient labeled observations for {spec['name']}: {len(samples)} available; "
            f"at least {_MIN_TRAINING_ROWS + _MIN_HOLDOUT_ROWS} are required."
        )

    split = max(_MIN_TRAINING_ROWS, int(len(samples) * 0.8))
    train_rows, test_rows = samples[:split], samples[split:]
    if len(test_rows) < _MIN_HOLDOUT_ROWS:
        raise ValueError(f"Chronological holdout requires at least {_MIN_HOLDOUT_ROWS} observations.")

    vectorizer = DictVectorizer(sparse=False, sort=True)
    x_train = vectorizer.fit_transform([item[1] for item in train_rows])
    x_test = vectorizer.transform([item[1] for item in test_rows])
    y_train = [item[2] for item in train_rows]
    y_test = [item[2] for item in test_rows]
    model = _estimator(spec["estimator"])
    model.fit(x_train, y_train)
    predictions = model.predict(x_test)
    baseline_value = sorted(y_train)[len(y_train) // 2]
    baseline = [baseline_value] * len(y_test)
    mae = float(mean_absolute_error(y_test, predictions))
    rmse = float(math.sqrt(mean_squared_error(y_test, predictions)))
    baseline_mae = float(mean_absolute_error(y_test, baseline))
    baseline_rmse = float(math.sqrt(mean_squared_error(y_test, baseline)))
    finite = all(math.isfinite(value) for value in (mae, rmse, baseline_mae, baseline_rmse))
    passed = finite and mae < baseline_mae and rmse < baseline_rmse

    artifact_buffer = io.BytesIO()
    joblib.dump({"vectorizer": vectorizer, "estimator": model, "model_key": model_key}, artifact_buffer)
    artifact = base64.b64encode(artifact_buffer.getvalue()).decode("ascii")
    fingerprint = hashlib.sha256(artifact_buffer.getvalue()).hexdigest()[:16]
    return {
        "metrics": {
            "observations": len(samples),
            "training_observations": len(train_rows),
            "test_observations": len(test_rows),
            "mae": round(mae, 8),
            "rmse": round(rmse, 8),
            "baseline_mae": round(baseline_mae, 8),
            "baseline_rmse": round(baseline_rmse, 8),
            "target": spec["target"],
            "estimator": spec["estimator"],
            "evaluation_method": "chronological_80_20_holdout",
            "feature_count": len(vectorizer.feature_names_),
            "last_training_close_utc": train_rows[-1][0].isoformat(),
            "first_test_close_utc": test_rows[0][0].isoformat(),
        },
        "validation": {
            "passed": passed,
            "checks": {
                "finite_metrics": finite,
                "beats_training_median_baseline_on_mae_and_rmse": bool(
                    finite and mae < baseline_mae and rmse < baseline_rmse
                ),
                "minimum_holdout_observations": len(test_rows) >= _MIN_HOLDOUT_ROWS,
                "no_post_trade_features": True,
                "chronological_split": train_rows[-1][0] < test_rows[0][0],
            },
            "note": "Research validation only. Model is stored for offline evaluation; it is not deployed to trading.",
        },
        "artifact": artifact,
        "version": f"trade-{fingerprint}",
    }
