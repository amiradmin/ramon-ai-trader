from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
import pickle
from typing import Mapping, Sequence

import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier


DIRECTION_V2_FEATURES = (
    "side_sell",
    "edge_ratio",
    "signal_strength",
    "uncertainty_atr",
    "intrabar_move_atr",
    "intrabar_rebound_atr",
    "ai_trend_score",
    "ai_trend_consistency",
    "spread_atr",
    "forecast_distance_atr",
    "ret_1_atr_aligned",
    "ret_4_atr_aligned",
    "ret_12_atr_aligned",
    "range_12_atr",
    "body_efficiency_12",
    "atr_pct",
    "momentum_accel_atr",
    "trend_persistence",
    "timing_alignment",
    "forecast_efficiency",
)


def direction_v2_features(base: Mapping[str, float]) -> dict[str, float]:
    """Build v2 features only from immutable live/training snapshot fields.

    The derived fields are scale-free and therefore do not inject absolute
    XAUUSD price levels into the classifier.
    """
    ret1 = float(base["ret_1_atr_aligned"])
    ret4 = float(base["ret_4_atr_aligned"])
    ret12 = float(base["ret_12_atr_aligned"])
    ai_score = float(base["ai_trend_score"])
    ai_consistency = float(base["ai_trend_consistency"])
    intrabar = float(base["intrabar_move_atr"])
    forecast_distance = float(base["forecast_distance_atr"])
    uncertainty = float(base["uncertainty_atr"])

    values = {name: float(base[name]) for name in DIRECTION_V2_FEATURES if name in base}
    values["momentum_accel_atr"] = ret1 - ret4 / 4.0
    values["trend_persistence"] = (ret1 + ret4 / 4.0 + ret12 / 12.0) / 3.0
    values["timing_alignment"] = intrabar * ai_score * max(ai_consistency, 0.0)
    values["forecast_efficiency"] = forecast_distance / max(uncertainty, 1e-6)

    for name in DIRECTION_V2_FEATURES:
        value = float(values[name])
        if not math.isfinite(value):
            raise ValueError(f"non-finite Direction AI v2 feature {name}")
        values[name] = value
    return values


@dataclass
class DirectionHGBEnsemble:
    feature_names: tuple[str, ...]
    models: list[HistGradientBoostingClassifier]
    metadata: dict[str, object]

    @classmethod
    def train(
        cls,
        rows: Sequence[Mapping[str, float]],
        labels: Sequence[int],
        *,
        seeds: Sequence[int] = (17, 29, 43),
        metadata: dict[str, object] | None = None,
    ) -> "DirectionHGBEnsemble":
        if len(rows) != len(labels) or len(rows) < 40:
            raise ValueError("Direction AI v2 needs at least 40 aligned samples")
        if set(int(v) for v in labels) != {0, 1}:
            raise ValueError("Direction AI v2 requires both win/loss classes")
        if min(labels.count(0), labels.count(1)) < 10:
            raise ValueError("Direction AI v2 needs at least 10 samples of each class")

        names = tuple(DIRECTION_V2_FEATURES)
        X = np.asarray(
            [[float(row[name]) for name in names] for row in rows],
            dtype=np.float32,
        )
        y = np.asarray(labels, dtype=np.int8)
        if not np.isfinite(X).all():
            raise ValueError("Direction AI v2 training matrix contains non-finite values")

        models: list[HistGradientBoostingClassifier] = []
        # Small leaves are intentional because each direction currently has a
        # modest live-trade sample count. L2 + shallow trees control variance.
        min_leaf = max(5, min(20, len(rows) // 12))
        for seed in seeds:
            model = HistGradientBoostingClassifier(
                learning_rate=0.035,
                max_iter=220,
                max_leaf_nodes=11,
                max_depth=4,
                min_samples_leaf=min_leaf,
                l2_regularization=1.0,
                early_stopping=False,
                random_state=int(seed),
            )
            model.fit(X, y)
            models.append(model)

        meta = dict(metadata or {})
        meta.update(
            {
                "architecture": "DirectionAI-v2-HistGradientBoosting",
                "ensemble_members": len(models),
                "seeds": [int(v) for v in seeds],
                "feature_count": len(names),
            }
        )
        return cls(feature_names=names, models=models, metadata=meta)

    def predict_proba(self, features: Mapping[str, float]) -> float:
        row = np.asarray(
            [[float(features[name]) for name in self.feature_names]],
            dtype=np.float32,
        )
        if not np.isfinite(row).all():
            raise ValueError("Direction AI v2 inference contains non-finite values")
        probabilities = [float(model.predict_proba(row)[0, 1]) for model in self.models]
        probability = float(np.mean(probabilities))
        if not math.isfinite(probability) or not 0.0 <= probability <= 1.0:
            raise ValueError("Direction AI v2 produced invalid probability")
        return probability

    def save(self, path: str | Path) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("wb") as handle:
            pickle.dump(
                {
                    "feature_names": self.feature_names,
                    "models": self.models,
                    "metadata": self.metadata,
                },
                handle,
                protocol=pickle.HIGHEST_PROTOCOL,
            )

    @classmethod
    def load(cls, path: str | Path) -> "DirectionHGBEnsemble":
        with Path(path).open("rb") as handle:
            payload = pickle.load(handle)
        model = cls(
            feature_names=tuple(payload["feature_names"]),
            models=list(payload["models"]),
            metadata=dict(payload.get("metadata", {})),
        )
        if model.feature_names != tuple(DIRECTION_V2_FEATURES):
            raise ValueError("Direction AI v2 feature schema mismatch")
        if len(model.models) < 2:
            raise ValueError("Direction AI v2 ensemble is incomplete")
        return model


def classification_metrics(probabilities: Sequence[float], labels: Sequence[int]) -> dict[str, float]:
    if len(probabilities) != len(labels) or not labels:
        raise ValueError("invalid Direction AI v2 evaluation inputs")
    tp = tn = fp = fn = 0
    brier = 0.0
    for probability, raw_label in zip(probabilities, labels):
        label = int(raw_label)
        prediction = int(float(probability) >= 0.5)
        brier += (float(probability) - label) ** 2
        if label == 1 and prediction == 1:
            tp += 1
        elif label == 0 and prediction == 0:
            tn += 1
        elif label == 0:
            fp += 1
        else:
            fn += 1
    positive_recall = tp / max(tp + fn, 1)
    negative_recall = tn / max(tn + fp, 1)
    return {
        "balanced_accuracy": 0.5 * (positive_recall + negative_recall),
        "accuracy": (tp + tn) / len(labels),
        "brier": brier / len(labels),
        "positive_recall": positive_recall,
        "negative_recall": negative_recall,
        "samples": len(labels),
    }


def walk_forward_validate(
    rows: Sequence[Mapping[str, float]],
    labels: Sequence[int],
    *,
    folds: int = 3,
) -> dict[str, object]:
    """Expanding-window validation with no random train/test shuffle."""
    if len(rows) != len(labels) or len(rows) < 60:
        raise ValueError("Direction AI v2 walk-forward needs >=60 samples")
    if folds < 2:
        raise ValueError("Direction AI v2 walk-forward needs >=2 folds")

    n = len(rows)
    first_test = max(40, int(n * 0.60))
    remaining = n - first_test
    if remaining < folds * 5:
        raise ValueError("Direction AI v2 holdout window is too small")
    fold_size = max(5, remaining // folds)

    all_probabilities: list[float] = []
    all_labels: list[int] = []
    per_fold: list[dict[str, object]] = []

    for fold in range(folds):
        train_end = first_test + fold * fold_size
        test_end = n if fold == folds - 1 else min(n, train_end + fold_size)
        if test_end <= train_end:
            continue
        train_rows = rows[:train_end]
        train_labels = labels[:train_end]
        test_rows = rows[train_end:test_end]
        test_labels = labels[train_end:test_end]
        if set(int(v) for v in train_labels) != {0, 1}:
            continue
        model = DirectionHGBEnsemble.train(train_rows, train_labels)
        probabilities = [model.predict_proba(row) for row in test_rows]
        metrics = classification_metrics(probabilities, test_labels)
        per_fold.append({
            "fold": fold + 1,
            "train_samples": len(train_rows),
            "test_samples": len(test_rows),
            **metrics,
        })
        all_probabilities.extend(probabilities)
        all_labels.extend(int(v) for v in test_labels)

    if not all_labels:
        raise ValueError("Direction AI v2 produced no valid walk-forward folds")
    pooled = classification_metrics(all_probabilities, all_labels)
    return {"folds": per_fold, "pooled": pooled}
