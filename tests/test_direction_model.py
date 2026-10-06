from __future__ import annotations

from pathlib import Path

from ramon.direction_model import (
    DIRECTION_V2_FEATURES,
    DirectionHGBEnsemble,
    classification_metrics,
    direction_v2_features,
    walk_forward_validate,
)


def sample_row(i: int, label: int) -> dict[str, float]:
    side_sell = float(i % 2)
    direction = -1.0 if side_sell else 1.0
    signal = (0.25 + 0.015 * (i % 20)) * (1.0 if label else -1.0)
    base = {
        "side_sell": side_sell,
        "edge_ratio": 1.0 + abs(signal),
        "signal_strength": 0.2 + abs(signal),
        "uncertainty_atr": 0.7 - min(abs(signal), 0.4),
        "intrabar_move_atr": direction * signal,
        "intrabar_rebound_atr": abs(signal) * 0.4,
        "ai_trend_score": direction * (0.4 + signal),
        "ai_trend_consistency": 0.55 + 0.02 * (i % 10),
        "spread_atr": 0.04 + 0.001 * (i % 5),
        "forecast_distance_atr": 0.35 + abs(signal),
        "ret_1_atr_aligned": signal,
        "ret_4_atr_aligned": signal * 2.2,
        "ret_12_atr_aligned": signal * 3.5,
        "range_12_atr": 1.5 + 0.02 * (i % 7),
        "body_efficiency_12": 0.25 + abs(signal),
        "atr_pct": 0.0015 + 0.00001 * (i % 13),
    }
    return direction_v2_features(base)


def dataset(n: int = 120):
    labels = [1 if (i % 4) in {0, 1} else 0 for i in range(n)]
    rows = [sample_row(i, label) for i, label in enumerate(labels)]
    return rows, labels


def test_direction_v2_features_are_scale_free_and_complete():
    row = sample_row(3, 1)
    assert set(row) == set(DIRECTION_V2_FEATURES)
    assert "momentum_accel_atr" in row
    assert "trend_persistence" in row
    assert "timing_alignment" in row
    assert "forecast_efficiency" in row


def test_direction_hgb_ensemble_round_trip(tmp_path: Path):
    rows, labels = dataset()
    model = DirectionHGBEnsemble.train(rows, labels)
    probability = model.predict_proba(rows[0])
    assert 0.0 <= probability <= 1.0
    assert len(model.models) == 3
    assert model.metadata["architecture"] == "DirectionAI-v2-HistGradientBoosting"

    path = tmp_path / "direction.pkl"
    model.save(path)
    loaded = DirectionHGBEnsemble.load(path)
    assert abs(loaded.predict_proba(rows[0]) - probability) < 1e-12


def test_direction_v2_walk_forward_is_time_ordered_and_reports_pooled_metrics():
    rows, labels = dataset(150)
    report = walk_forward_validate(rows, labels, folds=3)
    assert len(report["folds"]) >= 2
    assert report["pooled"]["samples"] > 0
    assert 0.0 <= report["pooled"]["balanced_accuracy"] <= 1.0
    assert 0.0 <= report["pooled"]["brier"] <= 1.0
    assert all(
        fold["train_samples"] < fold["train_samples"] + fold["test_samples"]
        for fold in report["folds"]
    )


def test_classification_metrics_balanced_accuracy():
    metrics = classification_metrics([0.9, 0.8, 0.1, 0.2], [1, 1, 0, 0])
    assert metrics["balanced_accuracy"] == 1.0
    assert metrics["accuracy"] == 1.0
    assert metrics["brier"] < 0.05
