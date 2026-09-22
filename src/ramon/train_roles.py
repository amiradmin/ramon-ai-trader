from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3
from typing import Mapping, Sequence

from .core import Bar, atr14
from .ensemble import (
    ENTRY_FEATURES,
    META_BASE_FEATURES,
    META_FEATURES,
    REGIME_FEATURES,
    BinaryLogisticModel,
    balanced_accuracy,
    regime_features,
    train_binary_logistic,
)
from .history import ensure_history_db, load_bars


def _chronological_split(
    rows: Sequence[Mapping[str, float]],
    labels: Sequence[int],
) -> tuple[list[Mapping[str, float]], list[int], list[Mapping[str, float]], list[int]]:
    cut = max(20, int(len(rows) * 0.8))
    cut = min(cut, len(rows) - 10)
    return list(rows[:cut]), list(labels[:cut]), list(rows[cut:]), list(labels[cut:])


def _train_candidate(
    rows: Sequence[Mapping[str, float]],
    labels: Sequence[int],
    feature_names: Sequence[str],
    path: Path,
    *,
    role: str,
    minimum_samples: int,
) -> dict[str, object]:
    if len(rows) < minimum_samples:
        return {"role": role, "updated": False, "status": "waiting_for_samples", "samples": len(rows)}
    if min(labels.count(0), labels.count(1)) < 30:
        return {"role": role, "updated": False, "status": "class_imbalance", "samples": len(rows)}

    train_rows, train_labels, valid_rows, valid_labels = _chronological_split(rows, labels)
    model = train_binary_logistic(
        train_rows,
        train_labels,
        feature_names,
        metadata={"role": role, "samples": len(rows)},
    )
    score = balanced_accuracy(model, valid_rows, valid_labels)
    majority = max(sum(valid_labels), len(valid_labels) - sum(valid_labels)) / max(len(valid_labels), 1)
    baseline_balanced = 0.5

    if score < 0.52 or score <= baseline_balanced:
        return {
            "role": role,
            "updated": False,
            "status": "validation_rejected",
            "samples": len(rows),
            "balanced_accuracy": round(score, 4),
            "majority_accuracy": round(majority, 4),
        }

    metadata = dict(model.metadata)
    metadata.update(
        {
            "balanced_accuracy": score,
            "validation_samples": len(valid_rows),
            "positive_rate": sum(labels) / len(labels),
        }
    )
    promoted = BinaryLogisticModel(
        feature_names=model.feature_names,
        means=model.means,
        scales=model.scales,
        weights=model.weights,
        bias=model.bias,
        metadata=metadata,
    )
    promoted.save(path)
    return {
        "role": role,
        "updated": True,
        "status": "promoted",
        "samples": len(rows),
        "balanced_accuracy": round(score, 4),
    }


def _regime_dataset(bars: Sequence[Bar]) -> tuple[list[dict[str, float]], list[int]]:
    rows: list[dict[str, float]] = []
    labels: list[int] = []
    for index in range(32, len(bars) - 8):
        context = bars[: index + 1]
        atr = atr14(context)
        if atr <= 0:
            continue
        future = bars[index + 1 : index + 9]
        future_path = [bars[index].close, *[bar.close for bar in future]]
        net = future_path[-1] - future_path[0]
        gross = sum(abs(b - a) for a, b in zip(future_path, future_path[1:]))
        efficiency = abs(net) / max(gross, 1e-9)
        move_atr = abs(net) / atr
        label = int(move_atr >= 0.60 and efficiency >= 0.50)
        rows.append(regime_features(context))
        labels.append(label)
    return rows, labels


def _load_snapshot_rows(db: str | Path, symbol: str) -> list[dict[str, object]]:
    ensure_history_db(db)
    with sqlite3.connect(db) as conn:
        records = conn.execute(
            """
            SELECT captured,mid,spread,atr,direction,base_decision,
                   regime_features,entry_features,meta_base_features
            FROM decision_samples
            WHERE symbol=?
            ORDER BY captured
            """,
            (symbol,),
        ).fetchall()

    rows: list[dict[str, object]] = []
    for record in records:
        rows.append(
            {
                "captured": int(record[0]),
                "mid": float(record[1]),
                "spread": float(record[2]),
                "atr": float(record[3]),
                "direction": str(record[4]),
                "base_decision": str(record[5]),
                "regime": json.loads(record[6]),
                "entry": json.loads(record[7]),
                "meta_base": json.loads(record[8]),
            }
        )
    return rows


def _labeled_snapshots(records: Sequence[dict[str, object]]) -> tuple[list[dict[str, object]], list[int]]:
    examples: list[dict[str, object]] = []
    labels: list[int] = []
    future_index = 0
    for index, record in enumerate(records):
        target_time = int(record["captured"]) + 900
        future_index = max(future_index, index + 1)
        while future_index < len(records) and int(records[future_index]["captured"]) < target_time:
            future_index += 1
        if future_index >= len(records):
            break
        future = records[future_index]
        if int(future["captured"]) > target_time + 300:
            continue
        direction = str(record["direction"])
        sign = 1.0 if direction == "BUY" else -1.0
        move = sign * (float(future["mid"]) - float(record["mid"]))
        threshold = max(float(record["spread"]), 0.05 * float(record["atr"]))
        examples.append(record)
        labels.append(int(move > threshold))
    return examples, labels


def main() -> None:
    parser = argparse.ArgumentParser(description="Train Ramon's lightweight regime, entry and meta roles")
    parser.add_argument("--db", default="/data/ramon_history.sqlite3")
    parser.add_argument("--symbol", default="XAUUSD_l")
    parser.add_argument("--out", default="/checkpoints/ensemble")
    parser.add_argument("--regime-min-samples", type=int, default=300)
    parser.add_argument("--entry-min-samples", type=int, default=500)
    parser.add_argument("--meta-min-samples", type=int, default=500)
    args = parser.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    bars, _ = load_bars(args.db, args.symbol)
    regime_rows, regime_labels = _regime_dataset(bars)
    regime_report = _train_candidate(
        regime_rows,
        regime_labels,
        REGIME_FEATURES,
        out / "regime.json",
        role="regime",
        minimum_samples=args.regime_min_samples,
    )

    snapshots = _load_snapshot_rows(args.db, args.symbol)
    labeled, labels = _labeled_snapshots(snapshots)
    entry_rows = [dict(row["entry"]) for row in labeled]
    entry_report = _train_candidate(
        entry_rows,
        labels,
        ENTRY_FEATURES,
        out / "entry.json",
        role="entry",
        minimum_samples=args.entry_min_samples,
    )

    meta_report: dict[str, object]
    regime_path = out / "regime.json"
    entry_path = out / "entry.json"
    if regime_path.is_file() and entry_path.is_file() and len(labeled) >= args.meta_min_samples:
        regime_model = BinaryLogisticModel.load(regime_path)
        entry_model = BinaryLogisticModel.load(entry_path)
        meta_rows: list[dict[str, float]] = []
        for row in labeled:
            features = dict(row["meta_base"])
            features["regime_probability"] = regime_model.predict_proba(dict(row["regime"]))
            features["entry_probability"] = entry_model.predict_proba(dict(row["entry"]))
            meta_rows.append(features)
        meta_report = _train_candidate(
            meta_rows,
            labels,
            META_FEATURES,
            out / "meta.json",
            role="meta",
            minimum_samples=args.meta_min_samples,
        )
    else:
        meta_report = {
            "role": "meta",
            "updated": False,
            "status": "waiting_for_role_models_or_samples",
            "samples": len(labeled),
        }

    report = {
        "regime": regime_report,
        "entry": entry_report,
        "meta": meta_report,
        "decision_samples": len(snapshots),
        "labeled_samples": len(labeled),
        "updated": any(bool(item.get("updated")) for item in (regime_report, entry_report, meta_report)),
    }
    (out / "training_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
