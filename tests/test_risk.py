from __future__ import annotations

from math import log
from pathlib import Path

from ramon.ensemble import BinaryLogisticModel
from ramon.risk import (
    RISK_FEATURES,
    RiskModelCoordinator,
    risk_features,
    risk_multiplier,
)


def _snapshot() -> dict[str, dict[str, float]]:
    return {
        "entry": {
            "edge_ratio": 2.0,
            "signal_strength": 0.35,
            "uncertainty_atr": 2.0,
            "intrabar_move_atr": 0.1,
            "intrabar_rebound_atr": 0.1,
            "ai_trend_score": 0.4,
            "ai_trend_consistency": 1.0,
            "spread_atr": 0.05,
        },
        "regime": {
            "ret_4_atr": 0.8,
            "range_4_atr": 1.2,
            "body_efficiency_12": 0.7,
        },
        "news": {
            "high_impact_near": 0.0,
            "medium_impact_near": 0.2,
            "upcoming_high_60m": 0.0,
            "recent_high_60m": 0.0,
            "event_density_180m": 0.1,
        },
    }


def test_risk_feature_vector_is_complete() -> None:
    values = risk_features(_snapshot())
    assert tuple(values) == RISK_FEATURES


def test_probability_mapping_is_accelerator_only() -> None:
    assert risk_multiplier(0.40) == (1.0, "NORMAL")
    assert risk_multiplier(0.60) == (1.0, "NORMAL")
    assert risk_multiplier(0.75) == (1.5, "RISK_ON")
    assert risk_multiplier(0.90) == (2.0, "STRONG_RISK_ON")


def test_risk_model_is_learning_until_promoted(tmp_path: Path) -> None:
    coordinator = RiskModelCoordinator(tmp_path, "test/model")
    result = coordinator.assess(_snapshot(), symbol="XAUUSD_l", news_source_ready=True)
    assert result == {
        "risk_model_ready": 0,
        "risk_probability": -1.0,
        "risk_multiplier": 1.0,
        "risk_mode": "LEARNING",
    }


def test_high_impact_news_caps_promoted_model(tmp_path: Path) -> None:
    from hashlib import sha256
    import json

    root = tmp_path
    bundle_id = "risk-test"
    directory = root / "versions" / bundle_id
    directory.mkdir(parents=True)
    probability = 0.90
    model = BinaryLogisticModel(
        RISK_FEATURES,
        (0.0,) * len(RISK_FEATURES),
        (1.0,) * len(RISK_FEATURES),
        (0.0,) * len(RISK_FEATURES),
        log(probability / (1.0 - probability)),
        {},
    )
    model_path = directory / "risk.json"
    model.save(model_path)
    (directory / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "bundle_id": bundle_id,
                "chronos_model": "test/model",
                "symbol": "XAUUSD_l",
                "promotion_gate_passed": True,
                "sha256": sha256(model_path.read_bytes()).hexdigest(),
            }
        ),
        encoding="utf-8",
    )
    (root / "active.json").write_text(json.dumps({"bundle_id": bundle_id}), encoding="utf-8")

    snapshot = _snapshot()
    snapshot["news"]["high_impact_near"] = 0.8
    coordinator = RiskModelCoordinator(root, "test/model")
    result = coordinator.assess(snapshot, symbol="XAUUSD_l", news_source_ready=True)
    assert result["risk_model_ready"] == 1
    assert result["risk_probability"] > 0.8
    assert result["risk_multiplier"] == 1.0
    assert result["risk_mode"] == "NEWS_CAPPED"
