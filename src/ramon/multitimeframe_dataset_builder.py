"""Point-in-time Ramon multi-timeframe research dataset (no live integration).

One deterministic, earliest eligible snapshot per signal M15 bar. Closed M5,
M15 and constructed H1 only. Untraded samples have NULL labels, NOT losses.
Every realized outcome is linked via exact sample_key + symbol; no inferred
returns from later prices. Data sources are read-only.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

from .multitimeframe_h1_overlay import load_research_h1

STEPS = {"M5": 300, "M15": 900, "H1": 3600}
FIELDS = [
    "sample_key", "signal_bar_time", "decision_utc", "direction", "base_decision",
    "outcome_status", "net_r", "net_units", "label_end_utc",
    *[f"{tf.lower()}_{name}" for tf in STEPS
      for name in ("last_close", "return_2bars", "last_range", "last_body")],
]


def _ohlc_valid(row):
    if row is None or len(row) != 5:
        return False
    _, o, h, l, c = row
    values = (o, h, l, c)
    return (all(math.isfinite(float(v)) and float(v) > 0 for v in values)
            and float(h) >= max(float(o), float(c))
            and float(l) <= min(float(o), float(c))
            and float(h) >= float(l))


def _closed_features(series, decision_utc, seconds):
    last_open = (decision_utc // seconds) * seconds - seconds
    a = series.get(last_open - seconds)
    b = series.get(last_open)
    if not (_ohlc_valid(a) and _ohlc_valid(b)):
        return None
    # Reject incomplete, stale, or non-contiguous source bars by requiring
    # exact expected open times, not merely two previous rows.
    if int(a[0]) != last_open - seconds or int(b[0]) != last_open:
        return None
    return (float(b[4]), float(b[4]) / float(a[4]) - 1,
            float(b[2]) - float(b[3]), float(b[4]) - float(b[1]))


def build_dataset(decisions, outcomes, candles):
    selected = {}
    counters = Counter()
    # Select one earliest captured candidate per signal bar FIRST, before
    # observing outcome labels or context completeness. No cherry-picking.
    for row in sorted(decisions, key=lambda r: (int(r["captured"]), int(r["id"]))):
        bar = row["signal_bar_time"]
        if bar is None or row["captured"] is None or not str(row.get("sample_key") or "").strip():
            counters["missing_decision_identity"] += 1
            continue
        bar = int(bar)
        if bar in selected:
            counters["duplicate_snapshots_excluded"] += 1
            continue
        captured = int(row["captured"])
        if bar <= 0 or captured < bar:
            counters["invalid_signal_bar_time"] += 1
            continue
        selected[bar] = row
    by_key = defaultdict(list)
    for row in outcomes:
        by_key[(row["symbol"], row["sample_key"])].append(row)
    result = []
    for bar in sorted(selected):
        d = selected[bar]
        when = int(d["captured"])
        values = {}
        for tf, seconds in STEPS.items():
            features = _closed_features(candles[tf], when, seconds)
            if features is None:
                counters[f"{tf}_missing_context"] += 1
                break
            values.update(dict(zip(
                (f"{tf.lower()}_last_close", f"{tf.lower()}_return_2bars",
                 f"{tf.lower()}_last_range", f"{tf.lower()}_last_body"),
                features,
            )))
        else:
            linked = by_key[(d["symbol"], d["sample_key"])]
            status = "UNTRADED_OR_UNKNOWN"
            outcome_r = outcome_units = ended = ""
            if len(linked) == 1:
                o = linked[0]
                net_r, net_units = o["net_r"], o["net_units"]
                try:
                    end = int(o["closed"])
                    start = int(o["opened"])
                    valid = (end >= start >= when and math.isfinite(float(net_r))
                             and math.isfinite(float(net_units)))
                except (TypeError, ValueError, OverflowError):
                    valid = False
                if valid:
                    status = "REALIZED_TRADE"
                    outcome_r, outcome_units, ended = float(net_r), float(net_units), end
                    counters["realized_labels"] += 1
                else:
                    status = "INCOMPLETE_OR_NONCAUSAL_TRADE"
                    counters["invalid_outcomes"] += 1
            elif len(linked) > 1:
                status = "AMBIGUOUS_MULTIPLE_TRADES"
                counters["ambiguous_outcomes"] += 1
            else:
                counters["unlabeled_decisions"] += 1
            result.append({
                "sample_key": d["sample_key"], "signal_bar_time": bar,
                "decision_utc": when, "direction": d["direction"] or "",
                "base_decision": d["base_decision"] or "",
                "outcome_status": status, "net_r": outcome_r,
                "net_units": outcome_units, "label_end_utc": ended, **values,
            })
    return result, {
        "mode": "OFFLINE_MULTITIMEFRAME_DATASET_ONLY",
        "unique_signal_bars_selected": len(selected),
        "complete_context_rows": len(result),
        "counts": dict(sorted(counters.items())),
        "limitations": [
            "One earliest snapshot per signal bar, chosen without outcome knowledge.",
            "Untraded decisions have no hypothetical outcome label.",
            "Realized labels only for a single causal matched trade with finite net R and units.",
            "Subsequent snapshots on the same signal bar are excluded; their trades are not reassigned.",
            "This dataset is not an out-of-sample benchmark or forecast comparison.",
            "Underlying H1 is constructed research data, not native broker H1.",
        ],
    }


def read_inputs(db, h1_csv, symbol):
    h1 = load_research_h1(h1_csv)
    with sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        candles = {}
        for tf in ("M5", "M15"):
            bars = conn.execute("""
                SELECT time,open,high,low,close FROM history_bars
                WHERE symbol=? AND timeframe=? ORDER BY time
            """, (symbol, tf)).fetchall()
            # Fail on duplicate timestamps rather than silently replacing.
            mapping = {int(r[0]): tuple(r) for r in bars}
            if len(mapping) != len(bars):
                raise ValueError(f"duplicate {tf} bar timestamp")
            candles[tf] = mapping
        candles["H1"] = h1
        decisions = [dict(r) for r in conn.execute("""
            SELECT id,sample_key,symbol,signal_bar_time,captured,direction,base_decision
            FROM decision_samples WHERE symbol=? AND signal_bar_time IS NOT NULL
            ORDER BY captured,id
        """, (symbol,))]
        outcomes = [dict(r) for r in conn.execute("""
            SELECT sample_key,symbol,opened,closed,net_r,net_units
            FROM trade_outcomes WHERE symbol=?
        """, (symbol,))]
    return decisions, outcomes, candles


def export_dataset(db, h1_csv, *, symbol, output, report):
    if output.resolve() == report.resolve():
        raise ValueError("output and report must differ")
    decisions, outcomes, candles = read_inputs(db, h1_csv, symbol)
    rows, summary = build_dataset(decisions, outcomes, candles)
    summary["symbol"] = symbol
    summary["total_decision_snapshots"] = len(decisions)
    summary["total_trade_outcomes"] = len(outcomes)
    summary["output"] = str(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    report.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    report.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
                      encoding="utf-8")
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--h1-csv", type=Path, required=True)
    parser.add_argument("--symbol", default="XAUUSD_l")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(export_dataset(args.db, args.h1_csv, symbol=args.symbol,
                                    output=args.output, report=args.report),
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
