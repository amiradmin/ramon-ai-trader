"""Operational Direction AI specialist roles for Ramon."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
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
    "buy_quality": RISK_FEATURES,
    "sell_quality": RISK_FEATURES,
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

    def fit(name, rows, fitter):
        try:
            models[name] = fitter(rows)
            roles[name] = {"status": "experimental_ready", "samples": len(rows)}
        except (ValueError, KeyError, TypeError) as exc:
            roles[name] = {"status": "waiting_for_data", "samples": len(rows), "reason": str(exc)}

    fit("regime", regime_rows, lambda rows: fit_role(rows, "regime", FEATURES["regime"]))
    for role in ("entry", "news"):
        fit(role, training, lambda rows, role=role: fit_role(rows, role, FEATURES[role]))
    fit("risk", training, fit_risk_role)
    # Direction-quality roles are operational. Use every clean historical
    # trade so BUY and SELL remain observable with the largest valid sample.
    fit(
        "buy_quality",
        examples,
        lambda rows: fit_direction_quality_role(rows, "BUY"),
    )
    fit(
        "sell_quality",
        examples,
        lambda rows: fit_direction_quality_role(rows, "SELL"),
    )
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
        path = directory / f"{role}.json"
        model.save(path)
        hashes[role] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = {"mode": "direction_live", "schema_version": 1, "bundle_id": bundle_id,
                "chronos_model": chronos_model, "symbol": symbol, "roles": roles,
                "sha256": hashes, "closed_trade_samples": len(examples),
                "validation": "experimental_unvalidated", "promotion_gate_passed": False}
    atomic_json(directory / "manifest.json", manifest)
    atomic_json(root / "direction_live" / "current.json", {"bundle_id": bundle_id})
    return manifest


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
            if (manifest["mode"] not in {"direction_live", "shadow"} or manifest["schema_version"] != 1
                    or manifest["bundle_id"] != bundle_id
                    or manifest["chronos_model"] != chronos_model):
                raise ValueError("direction identity/checkpoint mismatch")
            self.manifest, self.bundle_id, self.symbol = manifest, bundle_id, manifest["symbol"]
            for role, expected in {**FEATURES, **DIRECTION_EXTRA_FEATURES}.items():
                if role not in manifest["sha256"]:
                    continue
                try:
                    path = directory / f"{role}.json"
                    if hashlib.sha256(path.read_bytes()).hexdigest() != manifest["sha256"][role]:
                        raise ValueError("checksum mismatch")
                    model = BinaryLogisticModel.load(path)
                    if model.feature_names != expected:
                        raise ValueError("feature schema mismatch")
                    setattr(self, role, model)
                except (OSError, ValueError, KeyError, TypeError) as exc:
                    self.role_errors[role] = str(exc)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            self.error = f"invalid_direction_bundle: {exc}"

    def status(self):
        return {**super().status(), "ensemble_mode": "direction_live", "ensemble_active": 1,
                "risk_model_ready": int(self.risk_ready),
                "direction_quality_live": int(self.buy_quality is not None and self.sell_quality is not None),
                "buy_quality_ready": int(self.buy_quality is not None),
                "sell_quality_ready": int(self.sell_quality is not None),
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
            "buy_quality", direction_risk_features(market, decision, "BUY")
        )
        sell_success_probability = predict(
            "sell_quality", direction_risk_features(market, decision, "SELL")
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
    print(json.dumps(train_direction_roles(args.db, Path(args.out), args.symbol, model), indent=2))


if __name__ == "__main__":
    main()
