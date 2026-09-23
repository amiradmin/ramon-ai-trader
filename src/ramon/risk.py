from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from math import isfinite
from pathlib import Path
from typing import Mapping

from .ensemble import BinaryLogisticModel


RISK_SCHEMA_VERSION = 1
RISK_FEATURES = (
    "edge_ratio",
    "signal_strength",
    "uncertainty_atr",
    "intrabar_move_atr",
    "intrabar_rebound_atr",
    "ai_trend_score",
    "ai_trend_consistency",
    "spread_atr",
    "ret_4_atr",
    "range_4_atr",
    "body_efficiency_12",
    "high_impact_near",
    "medium_impact_near",
    "upcoming_high_60m",
    "recent_high_60m",
    "event_density_180m",
)


def risk_features(snapshot: Mapping[str, Mapping[str, float]]) -> dict[str, float]:
    entry = snapshot["entry"]
    regime = snapshot["regime"]
    news = snapshot["news"]
    values = {
        "edge_ratio": float(entry["edge_ratio"]),
        "signal_strength": float(entry["signal_strength"]),
        "uncertainty_atr": float(entry["uncertainty_atr"]),
        "intrabar_move_atr": float(entry["intrabar_move_atr"]),
        "intrabar_rebound_atr": float(entry["intrabar_rebound_atr"]),
        "ai_trend_score": float(entry["ai_trend_score"]),
        "ai_trend_consistency": float(entry["ai_trend_consistency"]),
        "spread_atr": float(entry["spread_atr"]),
        "ret_4_atr": float(regime["ret_4_atr"]),
        "range_4_atr": float(regime["range_4_atr"]),
        "body_efficiency_12": float(regime["body_efficiency_12"]),
        "high_impact_near": float(news["high_impact_near"]),
        "medium_impact_near": float(news["medium_impact_near"]),
        "upcoming_high_60m": float(news["upcoming_high_60m"]),
        "recent_high_60m": float(news["recent_high_60m"]),
        "event_density_180m": float(news["event_density_180m"]),
    }
    if any(not isfinite(value) for value in values.values()):
        raise ValueError("non-finite risk feature")
    return values


def risk_multiplier(probability: float) -> tuple[float, str]:
    """Map validated model probability to a non-blocking sizing multiplier."""
    if probability < 0.70:
        return 1.00, "NORMAL"
    if probability < 0.82:
        return 1.50, "RISK_ON"
    return 2.00, "STRONG_RISK_ON"


@dataclass(frozen=True, slots=True)
class RiskBundle:
    bundle_id: str
    symbol: str
    chronos_model: str
    model: BinaryLogisticModel


def load_active_risk_bundle(root: str | Path, chronos_model: str | None = None) -> RiskBundle:
    root = Path(root)
    pointer = json.loads((root / "active.json").read_text(encoding="utf-8"))
    bundle_id = str(pointer["bundle_id"])
    directory = root / "versions" / bundle_id
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    if int(manifest["schema_version"]) != RISK_SCHEMA_VERSION:
        raise ValueError("risk bundle schema mismatch")
    if str(manifest["bundle_id"]) != bundle_id:
        raise ValueError("risk bundle identity mismatch")
    if not manifest.get("promotion_gate_passed"):
        raise ValueError("risk bundle has not passed promotion gates")
    if chronos_model is not None and str(manifest["chronos_model"]) != chronos_model:
        raise ValueError("risk bundle belongs to a different Chronos checkpoint")
    path = directory / "risk.json"
    if hashlib.sha256(path.read_bytes()).hexdigest() != str(manifest["sha256"]):
        raise ValueError("risk model checksum mismatch")
    model = BinaryLogisticModel.load(path)
    if model.feature_names != RISK_FEATURES:
        raise ValueError("risk feature schema mismatch")
    return RiskBundle(bundle_id, str(manifest["symbol"]), str(manifest["chronos_model"]), model)


class RiskModelCoordinator:
    """Sizing-only learned role. It never changes BUY/SELL/WAIT decisions."""

    def __init__(self, root: str | Path, chronos_model: str | None = None) -> None:
        self.root = Path(root)
        self.bundle: RiskBundle | None = None
        self.error = ""
        if (self.root / "active.json").exists():
            try:
                self.bundle = load_active_risk_bundle(self.root, chronos_model)
            except (OSError, ValueError, KeyError, TypeError) as exc:
                self.error = f"invalid_risk_bundle: {exc}"

    @property
    def ready(self) -> bool:
        return self.bundle is not None

    def status(self) -> dict[str, object]:
        return {
            "risk_model_ready": self.ready,
            "risk_bundle": self.bundle.bundle_id if self.bundle else "",
            "risk_model_error": self.error,
            "risk_model_mode": "learned_sizing" if self.ready else ("blocked" if self.error else "learning"),
        }

    def assess(
        self,
        snapshot: Mapping[str, Mapping[str, float]],
        *,
        symbol: str,
        news_source_ready: bool,
    ) -> dict[str, object]:
        if not self.ready:
            return {
                "risk_model_ready": 0,
                "risk_probability": -1.0,
                "risk_multiplier": 1.0,
                "risk_mode": "LEARNING" if not self.error else "BLOCKED",
            }

        assert self.bundle is not None
        if symbol != self.bundle.symbol:
            return {
                "risk_model_ready": 1,
                "risk_probability": -1.0,
                "risk_multiplier": 1.0,
                "risk_mode": "SYMBOL_CAPPED",
            }

        features = risk_features(snapshot)
        probability = self.bundle.model.predict_proba(features)
        multiplier, mode = risk_multiplier(probability)

        # Hard safety layer: learned sizing may not lever up when calendar context is
        # unavailable or a high-impact USD release is close. This does not alter direction.
        if (
            not news_source_ready
            or features["high_impact_near"] >= 0.60
            or features["upcoming_high_60m"] >= 0.50
        ):
            multiplier = min(multiplier, 1.0)
            mode = "NEWS_CAPPED"

        return {
            "risk_model_ready": 1,
            "risk_probability": probability,
            "risk_multiplier": multiplier,
            "risk_mode": mode,
        }
