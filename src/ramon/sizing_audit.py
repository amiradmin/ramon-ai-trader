"""Read-only comparison of trades with and without the EA minimum-lot risk override.

The EA sizing flag lives in Ramon_Signals.csv rather than SQLite. Historical CSV
rows have no sample key, so this module only accepts strict timestamp candidates
and labels the result as candidate evidence rather than an exact join.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import math
import os
from pathlib import Path
import sqlite3
import statistics
import sys

from .audit import decode_signal_row
from .report import load_report_trades


DEFAULT_MATCH_SECONDS = 5


def _signal_timestamp(value: str) -> int | None:
    try:
        return int(
            datetime.strptime(value, "%Y.%m.%d %H:%M:%S")
            .replace(tzinfo=timezone.utc)
            .timestamp()
        )
    except (TypeError, ValueError):
        return None


def read_signal_rows(stream, symbol: str) -> list[dict]:
    """Read trustworthy sizing fields from current or reconstructable legacy CSV rows."""
    reader = csv.reader(stream)
    header = next(reader, [])
    if header:
        header[0] = header[0].lstrip("\ufeff")
    rows: list[dict] = []
    for raw in reader:
        if not raw:
            continue
        if raw[0].lstrip("\ufeff") == "captured":
            header = [raw[0].lstrip("\ufeff"), *raw[1:]]
            continue
        row, evidence = decode_signal_row(header, raw)
        if row.get("symbol") != symbol:
            continue
        if evidence.get("csv_schema_status") == "UNREADABLE_LAYOUT":
            continue
        stamp = _signal_timestamp(row.get("captured", ""))
        override = row.get("min_lot_override_used")
        if stamp is None or override not in {"YES", "NO"}:
            continue
        rows.append({**row, **evidence, "_stamp": stamp})
    return rows


def load_closed_trades(db: str, symbol: str) -> list[dict]:
    """Load trade outcomes plus the stored quote timestamp without modifying the DB."""
    uri = Path(db).expanduser().resolve().as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as con:
        con.row_factory = sqlite3.Row
        con.execute("BEGIN")
        trades = load_report_trades(con, symbol)
        columns = {row[1] for row in con.execute("PRAGMA table_info(decision_samples)")}
        quotes: dict[str, int | None] = {}
        if {"sample_key", "quote_time"} <= columns:
            quotes = {
                str(row["sample_key"]): row["quote_time"]
                for row in con.execute(
                    "SELECT sample_key, quote_time FROM decision_samples WHERE symbol=?",
                    (symbol,),
                )
            }
        for trade in trades:
            trade["quote_time"] = quotes.get(str(trade.get("sample_key")))
        return trades


def _fingerprint(row: dict) -> tuple:
    return (
        row.get("_stamp"),
        row.get("decision"),
        row.get("min_lot_override_used"),
        row.get("risk_budget_units"),
        row.get("min_lot_sl_units"),
        row.get("planned_volume"),
    )


def match_override_candidates(
    trades: list[dict],
    signal_rows: list[dict],
    *,
    window_seconds: int = DEFAULT_MATCH_SECONDS,
) -> tuple[list[tuple[dict, dict]], dict[str, int]]:
    """Strictly match same-direction CSV candidates near the persisted quote time."""
    if window_seconds < 0:
        raise ValueError("window_seconds must be >= 0")
    matches: list[tuple[dict, dict]] = []
    coverage = {
        "closed_trades": len(trades),
        "quote_time_available": 0,
        "matched": 0,
        "ambiguous": 0,
        "unmatched": 0,
    }
    for trade in trades:
        quote = trade.get("quote_time")
        if quote is None:
            coverage["unmatched"] += 1
            continue
        coverage["quote_time_available"] += 1
        candidates = [
            row
            for row in signal_rows
            if row.get("decision") == trade.get("direction")
            and abs(int(row["_stamp"]) - int(quote)) <= window_seconds
        ]
        if not candidates:
            coverage["unmatched"] += 1
            continue
        distance = min(abs(int(row["_stamp"]) - int(quote)) for row in candidates)
        closest = [
            row for row in candidates
            if abs(int(row["_stamp"]) - int(quote)) == distance
        ]
        unique = {_fingerprint(row) for row in closest}
        if len(unique) != 1:
            coverage["ambiguous"] += 1
            continue
        matches.append((trade, closest[0]))
        coverage["matched"] += 1
    return matches, coverage


def _number(value: object) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def group_stats(matches: list[tuple[dict, dict]]) -> dict[str, dict]:
    """Aggregate realized performance by the historical EA override flag."""
    groups: dict[str, list[tuple[dict, dict]]] = {"YES": [], "NO": []}
    for pair in matches:
        groups[pair[1]["min_lot_override_used"]].append(pair)

    result: dict[str, dict] = {}
    for flag, rows in groups.items():
        risks = [float(trade["initial_risk_units"]) for trade, _ in rows]
        ratios: list[float] = []
        for trade, signal in rows:
            budget = _number(signal.get("risk_budget_units"))
            risk = _number(trade.get("initial_risk_units"))
            if budget is not None and budget > 0 and risk is not None:
                ratios.append(risk / budget)
        wins = sum(float(trade["net_units"]) > 0 for trade, _ in rows)
        losses = sum(float(trade["net_units"]) < 0 for trade, _ in rows)
        result[flag] = {
            "trades": len(rows),
            "wins": wins,
            "losses": losses,
            "win_rate": 100.0 * wins / len(rows) if rows else None,
            "net_units": sum(float(trade["net_units"]) for trade, _ in rows),
            "avg_r": statistics.mean(float(trade["net_r"]) for trade, _ in rows) if rows else None,
            "avg_initial_risk_units": statistics.mean(risks) if risks else None,
            "avg_risk_to_budget": statistics.mean(ratios) if ratios else None,
        }
    return result


def print_report(db: str, symbol: str, stream, *, window_seconds: int = DEFAULT_MATCH_SECONDS) -> None:
    trades = load_closed_trades(db, symbol)
    rows = read_signal_rows(stream, symbol)
    matches, coverage = match_override_candidates(
        trades, rows, window_seconds=window_seconds
    )
    stats = group_stats(matches)

    print("=== MIN-LOT OVERRIDE PERFORMANCE (STRICT CSV CANDIDATE MATCH) ===")
    print(
        f"Closed trades={coverage['closed_trades']} | quote_time={coverage['quote_time_available']} "
        f"| matched={coverage['matched']} | ambiguous={coverage['ambiguous']} "
        f"| unmatched={coverage['unmatched']}"
    )
    print(
        f"Match rule: same symbol + executed direction + closest CSV row within +/-{window_seconds}s "
        "of stored quote_time."
    )
    print(
        "CSV has no sample_key; these are candidate matches, not exact-ID joins. "
        "Unmatched/ambiguous trades are excluded."
    )
    for flag in ("YES", "NO"):
        row = stats[flag]
        if not row["trades"]:
            print(f"override={flag:3} | trades=0")
            continue
        ratio = (
            f"{row['avg_risk_to_budget']:.3f}x"
            if row["avg_risk_to_budget"] is not None
            else "UNKNOWN"
        )
        print(
            f"override={flag:3} | trades={row['trades']:3d} "
            f"| W/L={row['wins']}/{row['losses']} "
            f"| WR={row['win_rate']:.2f}% "
            f"| net={row['net_units']:+.4f} "
            f"| avgR={row['avg_r']:+.4f} "
            f"| avgRisk={row['avg_initial_risk_units']:.4f} "
            f"| avgRisk/budget={ratio}"
        )
    print("Descriptive only: small/non-random samples are not evidence that the override causes performance differences.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=os.getenv("RAMON_REPORT_DB", "/data/ramon_history.sqlite3"))
    parser.add_argument("--symbol", default=os.getenv("RAMON_REPORT_SYMBOL", "XAUUSD_l"))
    parser.add_argument("--signal-csv", default="-", help="CSV path, or - for stdin")
    parser.add_argument(
        "--window-seconds",
        type=int,
        default=int(os.getenv("RAMON_OVERRIDE_MATCH_SECONDS", str(DEFAULT_MATCH_SECONDS))),
    )
    args = parser.parse_args()
    if args.signal_csv == "-":
        print_report(args.db, args.symbol, sys.stdin, window_seconds=args.window_seconds)
    else:
        with open(args.signal_csv, encoding="utf-8-sig", errors="replace", newline="") as stream:
            print_report(args.db, args.symbol, stream, window_seconds=args.window_seconds)


if __name__ == "__main__":
    main()
