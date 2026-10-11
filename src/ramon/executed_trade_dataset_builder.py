"""Offline executed-trade-first M5/M15/H1 research export.

Keeps one row per realized trade rather than selecting the first decision of
each M15 signal bar. Never uses outcome fields to build historical features.
No live trading integrations, data mutation, or hypothetical trade labels.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

from .multitimeframe_dataset_builder import (
    STEPS, _closed_features,
)
from .multitimeframe_h1_overlay import load_research_h1

FIELDS = [
    "trade_key", "sample_key", "symbol", "decision_utc",
    "signal_bar_time", "signal_bar_utc", "opened_utc", "closed_utc",
    "direction", "exit_reason", "training_status",
    "net_units", "net_r",
    *[f"{tf.lower()}_{name}" for tf in STEPS
      for name in ("last_close", "return_2bars", "last_range", "last_body")],
]


def _valid_offset(seconds):
    if not isinstance(seconds, int) or abs(seconds) > 14 * 3600 or seconds % 60:
        raise ValueError("broker UTC offset must be a whole-minute integer within +/-14 hours")
    return seconds


def build_executed_trade_dataset(decisions, trades, candles, *, broker_utc_offset_seconds=0):
    offset = _valid_offset(broker_utc_offset_seconds)
    indexed = defaultdict(list)
    for d in decisions:
        if d["symbol"] and d["sample_key"]:
            indexed[(d["symbol"], d["sample_key"])].append(d)
    seen_trade_keys = set()
    counts = Counter()
    accepted = []
    for t in sorted(trades, key=lambda t: (int(t["opened"]), str(t["trade_key"]))):
        counts["trades_seen"] += 1
        key = t["trade_key"]
        if not key or key in seen_trade_keys:
            counts["duplicate_or_missing_trade_key"] += 1
            continue
        seen_trade_keys.add(key)
        linked = indexed.get((t["symbol"], t["sample_key"]), [])
        if not linked:
            counts["unmatched_trade"] += 1
            continue
        if len(linked) != 1:
            counts["ambiguous_decision"] += 1
            continue
        d = linked[0]
        try:
            captured, opened, closed = [int(v) for v in (
                d["captured"], t["opened"], t["closed"]
            )]
            signal_bar_raw = int(d["signal_bar_time"])
            signal_bar_utc = signal_bar_raw - offset
            net_r, net_units = float(t["net_r"]), float(t["net_units"])
            is_valid = (
                0 < signal_bar_utc <= captured <= opened <= closed
                and math.isfinite(net_r) and math.isfinite(net_units)
                and d["direction"] == t["direction"]
            )
        except (ValueError, TypeError, OverflowError):
            is_valid = False
        if not is_valid:
            counts["invalid_or_noncausal_match"] += 1
            continue
        counts["causal_joined_trades"] += 1
        features = {}
        for tf, seconds in STEPS.items():
            values = _closed_features(candles[tf], captured, seconds)
            if values is None:
                counts[f"{tf}_missing_context"] += 1
                break
            for name, value in zip(
                ("last_close", "return_2bars", "last_range", "last_body"), values
            ):
                features[f"{tf.lower()}_{name}"] = value
        else:
            accepted.append({
                "trade_key": key, "sample_key": t["sample_key"],
                "symbol": t["symbol"], "decision_utc": captured,
                "signal_bar_time": signal_bar_raw,
                "signal_bar_utc": signal_bar_utc,
                "opened_utc": opened, "closed_utc": closed,
                "direction": t["direction"], "exit_reason": t["exit_reason"] or "",
                "training_status": t["training_status"] or "",
                "net_units": net_units, "net_r": net_r, **features,
            })
            counts["complete_context_trades"] += 1
    return accepted, {
        "mode": "OFFLINE_EXECUTED_TRADE_DATASET_ONLY",
        "broker_utc_offset_seconds": offset,
        "counts": dict(sorted(counts.items())),
        "rows": len(accepted),
        "limitations": [
            "Only actual closed, uniquely linked trades; no simulated outcomes.",
            "Features are built from exactly two completed candles at decision UTC.",
            "Trade P/L and closure time are outcomes, not input features.",
            "Some otherwise valid trades fall outside historical M5/M15/H1 overlap.",
            "Derived H1 is a research proxy, not native broker H1.",
            "A complete row is not evidence of strategy profitability.",
            "Purged walk-forward and split by closed_utc are required before model evaluation.",
        ],
    }


def load_inputs(db: Path, h1_csv: Path, *, symbol: str):
    h1 = load_research_h1(h1_csv)
    with sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True) as con:
        con.row_factory = sqlite3.Row
        bars = {"H1": h1}
        for tf in ("M5", "M15"):
            records = con.execute(
                "SELECT time,open,high,low,close FROM history_bars "
                "WHERE symbol=? AND timeframe=? ORDER BY time",
                (symbol, tf),
            ).fetchall()
            mapping = {int(row[0]): tuple(row) for row in records}
            if len(mapping) != len(records):
                raise ValueError(f"duplicate {tf} candle")
            bars[tf] = mapping
        decisions = [dict(r) for r in con.execute(
            "SELECT sample_key,symbol,signal_bar_time,captured,direction "
            "FROM decision_samples WHERE symbol=?",
            (symbol,),
        )]
        trades = [dict(r) for r in con.execute(
            "SELECT trade_key,sample_key,symbol,opened,closed,direction,"
            "exit_reason,training_status,net_units,net_r "
            "FROM trade_outcomes WHERE symbol=?",
            (symbol,),
        )]
    return decisions, trades, bars


def export_executed_trades(db: Path, h1_csv: Path, *, symbol: str,
                           broker_utc_offset_seconds: int,
                           output: Path, report: Path):
    if output.resolve() == report.resolve():
        raise ValueError("output and report paths must differ")
    decisions, trades, bars = load_inputs(db, h1_csv, symbol=symbol)
    rows, summary = build_executed_trade_dataset(
        decisions, trades, bars,
        broker_utc_offset_seconds=broker_utc_offset_seconds,
    )
    summary["symbol"] = symbol
    summary["decisions_scanned"] = len(decisions)
    summary["trades_scanned"] = len(trades)
    output.parent.mkdir(parents=True, exist_ok=True)
    report.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    report.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
                      encoding="utf-8")
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--h1-csv", required=True, type=Path)
    parser.add_argument("--symbol", default="XAUUSD_l")
    parser.add_argument("--broker-utc-offset-seconds", required=True, type=int)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(export_executed_trades(
        args.db, args.h1_csv, symbol=args.symbol,
        broker_utc_offset_seconds=args.broker_utc_offset_seconds,
        output=args.output, report=args.report,
    ), ensure_ascii=False))


if __name__ == "__main__":
    main()
