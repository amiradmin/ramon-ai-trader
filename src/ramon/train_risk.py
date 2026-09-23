"""Train Ramon's sizing-only risk role on real closed trades.

The model never creates a trade. It only proposes a bounded 0.75x..2.0x risk
multiplier for trades already accepted by the directional stack.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from uuid import uuid4

from .bundles import atomic_json
from .ensemble import balanced_accuracy, train_binary_logistic
from .risk import RISK_FEATURES, RISK_SCHEMA_VERSION, risk_features, risk_multiplier
from .train_roles import Example, load_trade_examples, temporal_windows


def _sizing_metrics(examples: list[Example], multipliers: list[float]) -> dict[str, float | int]:
    equity = peak = drawdown = 0.0
    risk_on = 0
    for row, multiplier in zip(examples, multipliers):
        equity += row.net_r * multiplier
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
        risk_on += int(multiplier > 1.0)
    return {
        "trades": len(examples),
        "risk_on_trades": risk_on,
        "net_r": equity,
        "max_drawdown_r": drawdown,
        "average_multiplier": (
            sum(multipliers) / len(multipliers) if multipliers else 0.0
        ),
    }


def _live_multiplier(probability: float, row: Example) -> float:
    multiplier, _ = risk_multiplier(probability)
    news = row.features["news"]
    if (
        float(news["high_impact_near"]) >= 0.60
        or float(news["upcoming_high_60m"]) >= 0.50
    ):
        return min(multiplier, 1.0)
    return multiplier


def _stage(root: Path, model, metadata: dict[str, object]) -> str:
    bundle_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]
    directory = root / "versions" / bundle_id
    directory.mkdir(parents=True, exist_ok=False)
    path = directory / "risk.json"
    model.save(path)
    manifest = {
        **metadata,
        "schema_version": RISK_SCHEMA_VERSION,
        "bundle_id": bundle_id,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }
    atomic_json(directory / "manifest.json", manifest)
    return bundle_id


def _activate(root: Path, bundle_id: str) -> None:
    pointer = root / "active.json"
    previous = json.loads(pointer.read_text()) if pointer.exists() else {}
    atomic_json(
        pointer,
        {
            "bundle_id": bundle_id,
            "previous_bundle_id": previous.get("bundle_id"),
            "activated_at": datetime.now(timezone.utc).isoformat(),
        },
    )


def train_risk_bundle(
    *,
    db: str | Path,
    symbol: str,
    chronos_model: str,
    out: Path,
    minimum_samples: int = 300,
    minimum_holdout_trades: int = 20,
    improvement: float = 0.50,
    maximum_drawdown: float = 8.0,
) -> dict[str, object]:
    if minimum_samples < 100 or minimum_holdout_trades < 10:
        raise ValueError("invalid risk training sample limits")
    if improvement <= 0 or maximum_drawdown <= 0:
        raise ValueError("invalid risk promotion limits")

    examples = load_trade_examples(db, symbol, chronos_model)
    report: dict[str, object] = {
        "updated": False,
        "closed_trade_samples": len(examples),
        "chronos_model": chronos_model,
        "symbol": symbol,
        "dataset_source": "real_closed_positions_net_of_deal_costs",
    }
    if len(examples) < minimum_samples:
        return {
            **report,
            "status": "waiting_for_closed_trades",
            "minimum_samples": minimum_samples,
        }

    base, meta, holdout = temporal_windows(examples)
    training = [*base, *meta]
    if len(training) < 80 or len(holdout) < minimum_holdout_trades:
        return {**report, "status": "insufficient_purged_windows"}

    state_path = out / "risk_evaluation_state.json"
    if state_path.exists():
        state = json.loads(state_path.read_text(encoding="utf-8"))
        holdout = [row for row in holdout if row.time > int(state["last_evaluated_end"])]
    if len(holdout) < minimum_holdout_trades:
        return {**report, "status": "waiting_for_fresh_holdout"}

    rows = [risk_features(row.features) for row in training]
    labels = [row.label for row in training]
    if min(labels.count(0), labels.count(1)) < 20:
        return {**report, "status": "waiting_for_training_classes"}

    model = train_binary_logistic(
        rows,
        labels,
        RISK_FEATURES,
        metadata={
            "role": "risk",
            "samples": len(training),
            "last_feature_time": max(row.time for row in training),
            "last_label_end": max(row.label_end for row in training),
        },
    )
    holdout_rows = [risk_features(row.features) for row in holdout]
    probabilities = [model.predict_proba(row) for row in holdout_rows]
    multipliers = [
        _live_multiplier(probability, row)
        for probability, row in zip(probabilities, holdout)
    ]

    candidate = _sizing_metrics(holdout, multipliers)
    baseline = _sizing_metrics(holdout, [1.0] * len(holdout))
    candidate["balanced_accuracy"] = balanced_accuracy(model, holdout_rows, [row.label for row in holdout])
    candidate["brier"] = sum(
        (probability - row.label) ** 2
        for probability, row in zip(probabilities, holdout)
    ) / len(holdout)

    reasons: list[str] = []
    if int(candidate["trades"]) < minimum_holdout_trades:
        reasons.append("insufficient_holdout_trades")
    if float(candidate["balanced_accuracy"]) < 0.52:
        reasons.append("weak_holdout_balanced_accuracy")
    if int(candidate["risk_on_trades"]) < 5:
        reasons.append("too_few_risk_on_holdout_trades")
    if float(candidate["net_r"]) < float(baseline["net_r"]) + improvement:
        reasons.append("no_sizing_net_r_improvement")
    if float(candidate["max_drawdown_r"]) > float(baseline["max_drawdown_r"]) + 1.0:
        reasons.append("sizing_drawdown_worse_than_baseline")
    if float(candidate["max_drawdown_r"]) > maximum_drawdown:
        reasons.append("holdout_drawdown_limit")

    atomic_json(
        state_path,
        {"last_evaluated_end": max(row.label_end for row in holdout)},
    )
    metadata = {
        "chronos_model": chronos_model,
        "symbol": symbol,
        "promotion_gate_passed": not reasons,
        "training_label_end": max(row.label_end for row in training),
        "holdout_start": min(row.time for row in holdout),
        "holdout_end": max(row.label_end for row in holdout),
        "candidate": candidate,
        "baseline_1x": baseline,
        "gate_reasons": reasons,
    }
    bundle_id = _stage(out, model, metadata)
    if not reasons:
        _activate(out, bundle_id)

    return {
        **report,
        **metadata,
        "bundle_id": bundle_id,
        "updated": not reasons,
        "status": "promoted" if not reasons else "validation_rejected",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="/data/ramon_history.sqlite3")
    parser.add_argument("--symbol", default="XAUUSD_l")
    parser.add_argument("--out", default="/checkpoints/risk")
    parser.add_argument("--chronos-model", default="autogluon/chronos-2-small")
    parser.add_argument("--minimum-samples", type=int, default=300)
    parser.add_argument("--minimum-holdout-trades", type=int, default=20)
    args = parser.parse_args()

    report = train_risk_bundle(
        db=args.db,
        symbol=args.symbol,
        chronos_model=args.chronos_model,
        out=Path(args.out),
        minimum_samples=args.minimum_samples,
        minimum_holdout_trades=args.minimum_holdout_trades,
    )
    Path(args.out).mkdir(parents=True, exist_ok=True)
    atomic_json(Path(args.out) / "training_report.json", report)
    print(json.dumps(report))


if __name__ == "__main__":
    main()
