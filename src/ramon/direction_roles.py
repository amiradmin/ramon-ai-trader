"""Operational Direction AI specialist roles for Ramon."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import pickle
from pathlib import Path
import re
from uuid import uuid4

from .bundles import FEATURES, atomic_json
from .ensemble import (
    RISK_FEATURES,
    BinaryLogisticModel,
    EnsembleCoordinator,
    direction_risk_features,
    dominant_direction,
    entry_features,
    meta_base_features,
    regime_features,
    risk_features,
)
from .direction_model import DIRECTION_V2_FEATURES, DirectionHGBEnsemble, direction_v2_features, walk_forward_validate
from .history import load_bars
from .news import neutral_news_features
from .train_roles import (
    Example,
    _regime_dataset,
    fit_direction_quality_role,
    fit_risk_role,
    fit_role,
    load_trade_examples,
    meta_features,
    temporal_windows,
)

DIRECTION_EXTRA_FEATURES = {
    "buy_quality": DIRECTION_V2_FEATURES,
    "sell_quality": DIRECTION_V2_FEATURES,
}


def train_direction_roles(db: str | Path, root: Path, symbol: str, chronos_model: str) -> dict:
    """Fit the specialist roles used by the live Direction AI path."""
    examples = [row for row in load_trade_examples(db, symbol, chronos_model)
                if set(FEATURES["news"]).issubset(row.features.get("news", {}))]
    bars, _ = load_bars(db, symbol)
    base, meta, _ = temporal_windows(examples) if len(examples) >= 100 else ([], [], [])
    use_meta = len(base) >= 40 and len(meta) >= 40
    training = base if use_meta else examples
    regime_rows = _regime_dataset(bars)
    if use_meta:
        regime_rows = [row for row in regime_rows if row.label_end < meta[0].time]
    models = {}
    roles = {}
    validation = {}

    def fit(name, rows, fitter):
        try:
            models[name] = fitter(rows)
            roles[name] = {"status": "live_ready", "samples": len(rows)}
        except (ValueError, KeyError, TypeError) as exc:
            roles[name] = {"status": "waiting_for_data", "samples": len(rows), "reason": str(exc)}

    fit("regime", regime_rows, lambda rows: fit_role(rows, "regime", FEATURES["regime"]))
    for role in ("entry", "news"):
        fit(role, training, lambda rows, role=role: fit_role(rows, role, FEATURES[role]))
    fit("risk", training, fit_risk_role)
    # Direction-quality roles are operational. Use every clean historical
    # trade so BUY and SELL remain observable with the largest valid sample.
    for direction, role_name in (("BUY", "buy_quality"), ("SELL", "sell_quality")):
        direction_rows = [row for row in examples if row.direction == direction]
        try:
            ordered = sorted(direction_rows, key=lambda row: row.time)
            feature_rows = [direction_v2_features(risk_features(row)) for row in ordered]
            labels = [row.label for row in ordered]
            validation[role_name] = walk_forward_validate(feature_rows, labels)
            models[role_name] = fit_direction_quality_v2(ordered, direction)
            roles[role_name] = {
                "status": "live_ready",
                "samples": len(ordered),
                "walk_forward": validation[role_name],
            }
        except (ValueError, KeyError, TypeError) as exc:
            roles[role_name] = {
                "status": "waiting_for_data",
                "samples": len(direction_rows),
                "reason": str(exc),
            }
    if use_meta and all(name in models for name in ("regime", "entry", "news")):
        meta_rows = [Example(row.time, row.label_end, {"meta": meta_features(row, models)}, row.label) for row in meta]
        fit("meta", meta_rows, lambda rows: fit_role(rows, "meta", FEATURES["meta"]))
    else:
        roles["meta"] = {"status": "waiting_for_data", "samples": len(meta), "reason": "needs ready base roles and >=40 purged later meta samples"}
    bundle_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]
    directory = root / "direction_live" / "versions" / bundle_id
    directory.mkdir(parents=True, exist_ok=False)
    hashes = {}
    for role, model in models.items():
        suffix = ".pkl" if role in {"buy_quality", "sell_quality"} else ".json"
        path = directory / f"{role}{suffix}"
        model.save(path)
        hashes[role] = hashlib.sha256(path.read_bytes()).hexdigest()
    quality_roles_ready = all(name in models for name in ("buy_quality", "sell_quality"))
    pooled_scores = [
        float(validation[name]["pooled"]["balanced_accuracy"])
        for name in ("buy_quality", "sell_quality")
        if name in validation
    ]
    pooled_briers = [
        float(validation[name]["pooled"]["brier"])
        for name in ("buy_quality", "sell_quality")
        if name in validation
    ]
    validation_passed = bool(
        quality_roles_ready
        and len(pooled_scores) == 2
        and min(pooled_scores) >= 0.50
        and max(pooled_briers) <= 0.30
    )
    manifest = {
        "mode": "direction_live",
        "schema_version": 2,
        "bundle_id": bundle_id,
        "direction_model": "DirectionAI-v2-HistGradientBoosting",
        "chronos_model": chronos_model,
        "symbol": symbol,
        "roles": roles,
        "sha256": hashes,
        "closed_trade_samples": len(examples),
        "validation": validation,
        "validation_policy": {
            "method": "expanding_walk_forward",
            "minimum_balanced_accuracy_each_side": 0.50,
            "maximum_brier_each_side": 0.30,
        },
        "promotion_gate_passed": validation_passed,
    }
    atomic_json(directory / "manifest.json", manifest)
    if validation_passed:
        atomic_json(root / "direction_live" / "current.json", {"bundle_id": bundle_id})
    return manifest



def fit_direction_quality_v2(examples: list[Example], direction: str) -> DirectionHGBEnsemble:
    """Train the operational v2 direction-quality ensemble for one side."""
    if direction not in {"BUY", "SELL"}:
        raise ValueError("Direction AI v2 direction must be BUY or SELL")
    rows = [row for row in examples if row.direction == direction]
    labels = [row.label for row in rows]
    role = f"{direction.lower()}_quality"
    if len(rows) < 40 or min(labels.count(0), labels.count(1)) < 10:
        raise ValueError(
            f"{role}: need >=40 samples and >=10 wins/losses for Direction AI v2"
        )
    feature_rows = [direction_v2_features(risk_features(row)) for row in rows]
    return DirectionHGBEnsemble.train(
        feature_rows,
        labels,
        metadata={
            "role": role,
            "target": "win",
            "direction": direction,
            "samples": len(rows),
            "last_feature_time": max(row.time for row in rows),
            "last_label_end": max(row.label_end for row in rows),
        },
    )


class DirectionCoordinator(EnsembleCoordinator):
    """Operational coordinator for Direction AI specialists."""

    def __init__(self, root: str | Path, chronos_model: str | None = None):
        self.root = Path(root)
        self.regime = self.entry = self.news = self.meta = self.risk = None
        self.buy_quality = self.sell_quality = None
        self.manifest = {}
        self.bundle_id = self.symbol = self.error = ""
        self.threshold = 0.65
        self.role_errors = {}
        pointer = self.root / "direction_live" / "current.json"
        if not pointer.exists():
            # One-release migration bridge for an already-trained pre-live bundle.
            pointer = self.root / "shadow" / "current.json"
        if not pointer.exists():
            self.error = "direction_roles_not_trained"
            return
        try:
            bundle_id = json.loads(pointer.read_text())["bundle_id"]
            if not isinstance(bundle_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", bundle_id):
                raise ValueError("invalid direction bundle ID")
            directory = pointer.parent / "versions" / bundle_id
            manifest = json.loads((directory / "manifest.json").read_text())
            if (manifest["mode"] not in {"direction_live", "shadow"} or manifest["schema_version"] not in {1, 2}
                    or manifest["bundle_id"] != bundle_id
                    or manifest["chronos_model"] != chronos_model):
                raise ValueError("direction identity/checkpoint mismatch")
            self.manifest, self.bundle_id, self.symbol = manifest, bundle_id, manifest["symbol"]
            for role, expected in {**FEATURES, **DIRECTION_EXTRA_FEATURES}.items():
                if role not in manifest["sha256"]:
                    continue
                try:
                    if role in {"buy_quality", "sell_quality"} and manifest.get("schema_version") == 2:
                        path = directory / f"{role}.pkl"
                        if hashlib.sha256(path.read_bytes()).hexdigest() != manifest["sha256"][role]:
                            raise ValueError("checksum mismatch")
                        model = DirectionHGBEnsemble.load(path)
                    else:
                        path = directory / f"{role}.json"
                        if hashlib.sha256(path.read_bytes()).hexdigest() != manifest["sha256"][role]:
                            raise ValueError("checksum mismatch")
                        model = BinaryLogisticModel.load(path)
                    if model.feature_names != expected:
                        raise ValueError("feature schema mismatch")
                    setattr(self, role, model)
                except (OSError, ValueError, KeyError, TypeError, pickle.UnpicklingError) as exc:
                    self.role_errors[role] = str(exc)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            self.error = f"invalid_direction_bundle: {exc}"

    def status(self):
        return {**super().status(), "ensemble_mode": "direction_live", "ensemble_active": 1,
                "risk_model_ready": int(self.risk_ready),
                "direction_quality_live": int(self.buy_quality is not None and self.sell_quality is not None),
                "buy_quality_ready": int(self.buy_quality is not None),
                "sell_quality_ready": int(self.sell_quality is not None),
                "direction_model": self.manifest.get("direction_model", "legacy-logistic"),
                "direction_schema_version": self.manifest.get("schema_version", 1),
                "roles": self.manifest.get("roles", {}),
                "role_errors": self.role_errors}

    def assess(self, market, decision, news_features=None):
        features = {"regime": regime_features(market.bars),
                    "entry": entry_features(market, decision),
                    "news": dict(news_features or neutral_news_features()),
                    "meta_base": meta_base_features(market, decision)}
        errors = dict(self.role_errors)

        def predict(role, values):
            model = getattr(self, role)
            if model is None or market.symbol != self.symbol:
                return -1.0
            try:
                result = model.predict_proba(values)
                if not math.isfinite(result) or not 0 <= result <= 1:
                    raise ValueError("invalid probability")
                return result
            except Exception as exc:
                errors[role] = f"{type(exc).__name__}: {exc}"
                return -1.0

        probabilities = {role: predict(role, features[role]) for role in ("regime", "entry", "news")}
        meta_input = {**features["meta_base"], **{name + "_probability": p for name, p in probabilities.items()}}
        probabilities["meta"] = predict("meta", meta_input) if all(p >= 0 for p in probabilities.values()) else -1.0
        risk_probability = predict("risk", risk_features(market, decision))
        buy_success_probability = predict(
            "buy_quality", direction_v2_features(direction_risk_features(market, decision, "BUY"))
        )
        sell_success_probability = predict(
            "sell_quality", direction_v2_features(direction_risk_features(market, decision, "SELL"))
        )
        p = probabilities["regime"]
        payload = {"base_decision": decision.decision, "base_reason": decision.reason,
                   "decision": decision.decision, "reason": decision.reason, "edge": decision.edge,
                   "ensemble_ready": int(self.ready), "ensemble_active": 1,
                   "ensemble_bundle": self.bundle_id,
                   "ensemble_mode": "direction_live",
                   "risk_model_ready": int(self.risk_ready),
                   "risk_probability": risk_probability,
                   "risk_target": "full_stop_loss", "risk_multiplier": 1.0,
                   "full_sl_probability": risk_probability,
                   "buy_success_probability": buy_success_probability,
                   "sell_success_probability": sell_success_probability,
                   "direction_quality_ready": int(
                       buy_success_probability >= 0 and sell_success_probability >= 0
                   ),
                   "regime_label": "UNAVAILABLE" if p < 0 else "TREND" if p >= 0.5 else "RANGE/UNCLEAR",
                   "candidate_direction": dominant_direction(decision),
                   "role_errors": errors, "roles": self.manifest.get("roles", {}),
                   "role_error": self.error,
                   **{name + "_probability": p for name, p in probabilities.items()}}
        return payload, features


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="/data/ramon_history.sqlite3")
    parser.add_argument("--out", default="/checkpoints/ensemble")
    parser.add_argument("--symbol", default="XAUUSD_l")
    parser.add_argument("--chronos-model", default="autogluon/chronos-2-small")
    parser.add_argument("--active-model-file", default="/checkpoints/active_model.txt")
    args = parser.parse_args()
    model_file = Path(args.active_model_file)
    model = model_file.read_text().strip() if model_file.exists() else args.chronos_model
    report = train_direction_roles(args.db, Path(args.out), args.symbol, model)
    print(json.dumps(report, indent=2))
    if not report.get("promotion_gate_passed"):
        print("Direction AI v2 candidate was trained but NOT promoted; previous live bundle remains active.")


if __name__ == "__main__":
    main()
