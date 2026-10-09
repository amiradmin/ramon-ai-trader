"""Read-only audit linking entry-time features to realized Ramon failure patterns.

This tool joins the MFE/MAE diagnostic cohort to persisted decision_samples,
flattens historical JSON feature payloads across schema versions, and reports
descriptive associations. It does not train, promote, or modify live behavior.
"""
from __future__ import annotations

import argparse
import json
import math
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean, median, pstdev

from .mfe_mae_audit import load_audit_rows


JSON_COLUMNS = (
    "regime_features",
    "entry_features",
    "meta_base_features",
    "news_features",
    "model_metadata",
    "target_structure",
)

REQUESTED_HINTS = (
    "intrabar",
    "ai_trend",
    "entry_probability",
    "entry_prob",
    "buy_edge",
    "sell_edge",
    "strength",
    "market_state",
    "regime",
    "atr",
    "spread",
    "chronos",
    "forecast",
    "slope",
)


def _finite(value) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _safe_json(raw):
    if raw in (None, ""):
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        value = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def flatten_scalars(value, prefix="", *, depth=0, max_depth=5) -> dict[str, object]:
    """Flatten scalar leaves while refusing unbounded/high-cardinality structures."""
    if depth > max_depth:
        return {}
    out: dict[str, object] = {}
    if not isinstance(value, dict):
        return out
    for key, item in value.items():
        name = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(item, dict):
            out.update(flatten_scalars(item, name, depth=depth + 1, max_depth=max_depth))
        elif isinstance(item, (str, bool, int, float)) or item is None:
            if isinstance(item, float) and not math.isfinite(item):
                continue
            out[name] = item
    return out


def _normalize_scalar(value):
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)) and _finite(value):
        return float(value)
    if value is None:
        return None
    text = str(value).strip()
    if text.upper() in {"YES", "TRUE"}:
        return 1.0
    if text.upper() in {"NO", "FALSE"}:
        return 0.0
    try:
        number = float(text)
    except ValueError:
        return text[:120]
    return number if math.isfinite(number) else text[:120]


def load_joined_rows(con: sqlite3.Connection, symbol: str) -> tuple[list[dict], dict]:
    audit_rows, coverage = load_audit_rows(con, symbol)
    if not audit_rows:
        return [], {**coverage, "feature_joined": 0}

    con.row_factory = sqlite3.Row
    joined: list[dict] = []
    missing_sample = 0
    for outcome in audit_rows:
        sample = con.execute(
            """SELECT captured,quote_time,signal_bar_time,mid,spread,atr,direction,
                      base_decision,final_decision,chronos_model,schema_version,
                      stop_distance,target_distance,bundle_id,
                      regime_features,entry_features,meta_base_features,news_features,
                      model_metadata,target_structure
               FROM decision_samples WHERE sample_key=? AND symbol=?""",
            (outcome["sample_key"], symbol),
        ).fetchone()
        if sample is None:
            missing_sample += 1
            continue

        features: dict[str, object] = {
            "core.spread": sample["spread"],
            "core.atr": sample["atr"],
            "core.schema_version": sample["schema_version"],
            "core.base_decision": sample["base_decision"],
            "core.final_decision": sample["final_decision"],
            "core.direction": sample["direction"],
            "core.chronos_model": sample["chronos_model"],
            "core.stop_distance": sample["stop_distance"],
            "core.target_distance": sample["target_distance"],
        }
        if _finite(sample["spread"]) and _finite(sample["atr"]) and float(sample["atr"]) > 0:
            features["derived.spread_atr"] = float(sample["spread"]) / float(sample["atr"])
        if _finite(sample["stop_distance"]) and _finite(sample["atr"]) and float(sample["atr"]) > 0:
            features["derived.stop_atr"] = float(sample["stop_distance"]) / float(sample["atr"])
        if _finite(sample["target_distance"]) and _finite(sample["stop_distance"]) and float(sample["stop_distance"]) > 0:
            features["derived.target_stop_ratio"] = float(sample["target_distance"]) / float(sample["stop_distance"])

        for column in JSON_COLUMNS:
            payload = _safe_json(sample[column])
            for key, value in flatten_scalars(payload, column).items():
                features[key] = value

        features = {k: _normalize_scalar(v) for k, v in features.items()}
        joined.append({
            **outcome,
            "captured": int(sample["captured"]) if sample["captured"] is not None else None,
            "quote_time": int(sample["quote_time"]) if sample["quote_time"] is not None else None,
            "signal_bar_time": int(sample["signal_bar_time"]),
            "features": features,
            "labels": {
                "direction_weak": outcome["diagnostic_bucket"] == "DIRECTION_WEAK",
                "adverse_first": outcome["sequence_0_5r"] == "ADVERSE_FIRST",
                "exit_left_edge": outcome["diagnostic_bucket"] == "EXIT_LEFT_EDGE",
                "timing_stress": outcome["diagnostic_bucket"] == "TIMING_STRESS",
                "loss": outcome["net_r"] < 0,
            },
        })
    return joined, {**coverage, "feature_joined": len(joined), "missing_decision_sample": missing_sample}


def _numeric_associations(rows: list[dict], label: str, min_coverage: int) -> list[dict]:
    by_feature: dict[str, list[tuple[float, bool, float]]] = defaultdict(list)
    for row in rows:
        target = bool(row["labels"][label])
        for key, value in row["features"].items():
            if isinstance(value, (int, float)) and _finite(value):
                by_feature[key].append((float(value), target, float(row["net_r"])))

    results = []
    for key, values in by_feature.items():
        if len(values) < min_coverage:
            continue
        positive = [v for v, y, _ in values if y]
        negative = [v for v, y, _ in values if not y]
        if len(positive) < 8 or len(negative) < 8:
            continue
        all_values = [v for v, _, _ in values]
        sd = pstdev(all_values)
        smd = (mean(positive) - mean(negative)) / sd if sd > 1e-12 else 0.0
        results.append({
            "feature": key,
            "coverage": len(values),
            "target_n": len(positive),
            "non_target_n": len(negative),
            "target_mean": mean(positive),
            "non_target_mean": mean(negative),
            "target_median": median(positive),
            "non_target_median": median(negative),
            "standardized_mean_difference": smd,
        })
    results.sort(key=lambda x: abs(x["standardized_mean_difference"]), reverse=True)
    return results


def _categorical_associations(rows: list[dict], label: str, min_coverage: int) -> list[dict]:
    feature_values: dict[str, list[tuple[str, bool, float]]] = defaultdict(list)
    for row in rows:
        target = bool(row["labels"][label])
        for key, value in row["features"].items():
            if isinstance(value, str):
                feature_values[key].append((value, target, float(row["net_r"])))

    output = []
    base = sum(bool(r["labels"][label]) for r in rows) / len(rows) if rows else 0.0
    for key, values in feature_values.items():
        if len(values) < min_coverage:
            continue
        counts = Counter(v for v, _, _ in values)
        if len(counts) < 2 or len(counts) > 20:
            continue
        groups = []
        for value, count in counts.items():
            if count < 8:
                continue
            subset = [(y, r) for v, y, r in values if v == value]
            hits = sum(y for y, _ in subset)
            rate = hits / count
            groups.append({
                "value": value,
                "n": count,
                "target_rate": rate,
                "lift_vs_base": rate / base if base > 0 else None,
                "mean_net_r": mean(r for _, r in subset),
            })
        if len(groups) >= 2:
            spread = max(g["target_rate"] for g in groups) - min(g["target_rate"] for g in groups)
            output.append({"feature": key, "coverage": len(values), "rate_spread": spread,
                           "groups": sorted(groups, key=lambda g: g["target_rate"], reverse=True)})
    output.sort(key=lambda x: x["rate_spread"], reverse=True)
    return output


def _requested_feature_inventory(rows: list[dict]) -> list[dict]:
    keys = sorted({key for row in rows for key in row["features"]})
    out = []
    for hint in REQUESTED_HINTS:
        matches = [k for k in keys if hint.lower() in k.lower()]
        out.append({"hint": hint, "matches": matches[:40], "match_count": len(matches)})
    return out


def summarize(rows: list[dict], coverage: dict, *, min_coverage: int = 40, top: int = 20) -> dict:
    labels = ("direction_weak", "adverse_first", "exit_left_edge", "timing_stress", "loss")
    label_summary = {}
    associations = {}
    for label in labels:
        count = sum(bool(r["labels"][label]) for r in rows)
        label_summary[label] = {
            "n": count,
            "rate": count / len(rows) if rows else None,
            "net_r_sum": sum(float(r["net_r"]) for r in rows if r["labels"][label]),
        }
        associations[label] = {
            "numeric_top": _numeric_associations(rows, label, min_coverage)[:top],
            "categorical_top": _categorical_associations(rows, label, min_coverage)[:top],
        }

    safeentry = {}
    for needle in ("intrabar_confirmed", "ai_trend_confirmed", "entry_timing_ready"):
        matches = sorted({k for r in rows for k in r["features"] if needle in k.lower()})
        safeentry[needle] = []
        for key in matches:
            values = [(r["features"].get(key), r) for r in rows if r["features"].get(key) is not None]
            if not values:
                continue
            for val in sorted({str(v) for v, _ in values}):
                subset = [r for v, r in values if str(v) == val]
                safeentry[needle].append({
                    "feature": key,
                    "value": val,
                    "n": len(subset),
                    "loss_rate": sum(r["labels"]["loss"] for r in subset) / len(subset),
                    "direction_weak_rate": sum(r["labels"]["direction_weak"] for r in subset) / len(subset),
                    "adverse_first_rate": sum(r["labels"]["adverse_first"] for r in subset) / len(subset),
                    "mean_net_r": mean(r["net_r"] for r in subset),
                })

    return {
        "status": "evaluated" if rows else "insufficient_coverage",
        "coverage": coverage,
        "label_summary": label_summary,
        "safeentry_ablation_candidates": safeentry,
        "requested_feature_inventory": _requested_feature_inventory(rows),
        "associations": associations,
        "guardrail": (
            "Associations are descriptive and may reflect regime/version confounding. "
            "Do not promote any feature or threshold from this audit alone; confirm with "
            "time-ordered out-of-sample ablation or walk-forward validation."
        ),
    }


def run(db: str | Path, symbol: str = "XAUUSD_l", *, min_coverage: int = 40, top: int = 20) -> dict:
    path = Path(db).expanduser().resolve()
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as con:
        rows, coverage = load_joined_rows(con, symbol)
    return {
        "mode": "READ_ONLY_ENTRY_FAILURE_AUDIT",
        "symbol": symbol,
        "summary": summarize(rows, coverage, min_coverage=min_coverage, top=top),
        "trades": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="data/ramon_history.sqlite3")
    parser.add_argument("--symbol", default="XAUUSD_l")
    parser.add_argument("--output", default="data/entry_failure_audit.json")
    parser.add_argument("--min-coverage", type=int, default=40)
    parser.add_argument("--top", type=int, default=20)
    args = parser.parse_args()
    if args.min_coverage < 10:
        parser.error("--min-coverage must be >= 10")
    if args.top < 1:
        parser.error("--top must be >= 1")
    report = run(args.db, args.symbol, min_coverage=args.min_coverage, top=args.top)
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
