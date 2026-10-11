"""Read-only coverage audit: reconcile Ramon outcomes with a recorded tick window.

Does NOT infer broker SL/TP levels, replay execution, or place orders.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sqlite3
from collections import Counter
from pathlib import Path


def tick_window(path: Path) -> dict:
    first = last = previous = None
    count = 0
    with path.open(newline="", encoding="utf-8-sig") as stream:
        for row in csv.DictReader(stream):
            stamp = int(row["time_ms"])
            bid, ask = float(row["bid"]), float(row["ask"])
            if stamp <= 0 or not all(map(math.isfinite, (bid, ask))) or bid <= 0 or ask <= bid:
                raise ValueError("invalid recorded quote")
            if previous is not None and stamp <= previous:
                raise ValueError("ticks must have unique, increasing time_ms")
            first = stamp if first is None else first
            last = previous = stamp
            count += 1
    if not count:
        raise ValueError("empty ticks")
    return {"first_ms": first, "last_ms": last, "tick_rows": count}


def coverage(db: Path, *, first_ms: int, last_ms: int, symbol: str = "XAUUSD_l") -> dict:
    if not (0 < first_ms <= last_ms and symbol):
        raise ValueError("invalid window")
    conn = sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        # Matches are constrained by sample_key and symbol, never by coincident timestamps.
        rows = conn.execute("""
            SELECT t.trade_key, t.opened, t.closed, t.direction, t.actual_fill_price,
                   t.net_r, t.training_status, t.exit_reason,
                   d.sample_key AS matched_sample, d.captured, d.stop_distance,
                   d.target_distance, d.target_structure,
                   g.sample_key AS matched_targets, g.tp1, g.tp2, g.tp3
              FROM trade_outcomes t
              LEFT JOIN decision_samples d
                ON t.sample_key=d.sample_key AND t.symbol=d.symbol
              LEFT JOIN target_outcomes g
                ON t.sample_key=g.sample_key AND t.trade_key=g.trade_key
             WHERE t.symbol=?
             ORDER BY t.opened, t.trade_key
        """, (symbol,)).fetchall()
    finally:
        conn.close()
    flags = Counter()
    reasons = Counter()
    in_window = 0
    for row in rows:
        opened, closed = int(row["opened"]), int(row["closed"])
        # Seconds-to-ms conversion; require entire realized trade span to be covered.
        inside = opened * 1000 >= first_ms and closed * 1000 <= last_ms
        if not inside:
            flags["outside_complete_tick_window"] += 1
            continue
        in_window += 1
        if row["matched_sample"] is None:
            flags["missing_decision_link"] += 1
        elif row["captured"] is None or int(row["captured"]) > opened:
            flags["decision_not_causal_at_open"] += 1
        else:
            flags["decision_link_valid"] += 1
        if row["matched_targets"] is not None:
            flags["matched_target_outcome"] += 1
        else:
            flags["missing_target_outcome"] += 1
        price = row["actual_fill_price"]
        if price is not None and math.isfinite(float(price)) and float(price) > 0:
            flags["actual_fill_price_present"] += 1
        else:
            flags["actual_fill_price_missing"] += 1
        for name in ("stop_distance", "target_distance"):
            value = row[name]
            if value is not None and math.isfinite(float(value)) and float(value) > 0:
                flags[name + "_present"] += 1
        reasons[str(row["exit_reason"] or "UNKNOWN")] += 1
    return {
        "mode": "READ_ONLY_COVERAGE_AUDIT",
        "symbol": symbol,
        "window_ms": {"first": first_ms, "last": last_ms},
        "total_trades_for_symbol": len(rows),
        "trades_fully_within_tick_window": in_window,
        "counts": dict(sorted(flags.items())),
        "exit_reasons_in_window": dict(sorted(reasons.items())),
        "limitations": [
            "Counts overlap; do not add individual field counts to infer eligible trades.",
            "Timestamp containment does not prove tick completeness or broker UTC provenance.",
            "Target_outcomes are post-trade analytics, NOT necessarily original broker TP/SL.",
            "stop_distance/target_distance are decision telemetry, NOT proof of placed broker levels.",
            "No realized or counterfactual TP/SL labels are generated.",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--ticks", type=Path, required=True)
    parser.add_argument("--symbol", default="XAUUSD_l")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    window = tick_window(args.ticks)
    report = {**window, **coverage(args.db, first_ms=window["first_ms"],
                                    last_ms=window["last_ms"], symbol=args.symbol)}
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered)


if __name__ == "__main__":
    main()
