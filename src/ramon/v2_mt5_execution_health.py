"""Operational, read-only execution-quality snapshots for active Ramon V2.

Consumes EA outcomes already delivered through trade_outbox -> /trades.
Never changes a trade, invokes MT5, or adjusts V2's decision engine.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import sqlite3
import tempfile
import time


def _finite(value):
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError, OverflowError):
        return None


def analyze(db_path, *, symbol="XAUUSD_l", limit=500, current_time=None):
    """Capture costs/exits/quality from latest realized EA trades, no look-ahead."""
    if limit < 1 or limit > 100_000:
        raise ValueError("limit must be between 1 and 100000")
    now = int(time.time()) if current_time is None else int(current_time)
    path = Path(db_path)
    if not path.is_file():
        return {"ready": False, "reason": "database_not_found", "symbol": symbol}
    try:
        con = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True, timeout=2)
        con.row_factory = sqlite3.Row
        with con:
            columns = {row[1] for row in con.execute("PRAGMA table_info(trade_outcomes)")}
            required = {"trade_key", "symbol", "closed", "net_units", "net_r", "exit_reason"}
            if not required.issubset(columns):
                return {"ready": False, "reason": "trade_outcomes_schema_missing", "symbol": symbol}
            optional = ("commission_units", "swap_units", "fee_units", "training_status",
                        "exit_detail", "actual_fill_price", "entry_source", "direction")
            selected = ", ".join(
                [*sorted(required), *(col for col in optional if col in columns)])
            rows = con.execute(
                f"SELECT {selected} FROM trade_outcomes "
                "WHERE symbol=? AND closed<=? ORDER BY closed DESC, trade_key DESC LIMIT ?",
                (symbol, now, limit)).fetchall()
    except (sqlite3.Error, OSError) as exc:
        return {"ready": False, "reason": "database_read_failed", "symbol": symbol,
                "error_type": type(exc).__name__}
    finally:
        if "con" in locals():
            con.close()

    if not rows:
        return {"ready": True, "symbol": symbol, "trade_count": 0,
                "last_closed_utc": None, "net_units": 0.0, "mean_r": None,
                "profit_factor": None, "win_rate": None,
                "cost_coverage": {}, "exit_reasons": {}, "training_statuses": {},
                "quality_warnings": ["no_closed_trades"]}

    def value(row, key):
        return row[key] if key in row.keys() else None

    net = [_finite(row["net_units"]) for row in rows]
    rvals = [_finite(row["net_r"]) for row in rows]
    valid_net = [v for v in net if v is not None]
    valid_r = [v for v in rvals if v is not None]
    positives = sum(x for x in valid_net if x > 0)
    negatives = -sum(x for x in valid_net if x < 0)
    count = len(valid_net)
    coverage = {}
    cost_totals = {}
    for key in ("commission_units", "swap_units", "fee_units"):
        values = [_finite(value(row, key)) for row in rows]
        observed = [v for v in values if v is not None]
        coverage[key] = {"observed": len(observed), "total": len(rows)}
        cost_totals[key] = round(sum(observed), 8) if observed else None
    quality_warnings = []
    if count < len(rows):
        quality_warnings.append("missing_or_nonfinite_net_units")
    if len(valid_r) < len(rows):
        quality_warnings.append("missing_or_nonfinite_net_r")
    if any(coverage[k]["observed"] < len(rows) for k in coverage):
        quality_warnings.append("partial_cost_telemetry")
    fill_observed = sum(_finite(value(row, "actual_fill_price")) is not None for row in rows)
    if fill_observed < len(rows):
        quality_warnings.append("partial_fill_price_telemetry")
    return {
        "ready": True, "engine": "V2_OBSERVABILITY", "live_order_access": False,
        "symbol": symbol, "trade_count": len(rows),
        "last_closed_utc": int(rows[0]["closed"]),
        "net_units": round(sum(valid_net), 8),
        "mean_r": sum(valid_r) / len(valid_r) if valid_r else None,
        "profit_factor": positives / negatives if negatives > 0 else None,
        "win_rate": sum(x > 0 for x in valid_net) / count if count else None,
        "cost_coverage": coverage, "observed_cost_totals": cost_totals,
        "fill_price_coverage": {"observed": fill_observed, "total": len(rows)},
        "exit_reasons": dict(Counter(str(row["exit_reason"]) for row in rows)),
        "training_statuses": dict(Counter(str(value(row, "training_status") or "UNKNOWN") for row in rows)),
        "quality_warnings": quality_warnings,
        "note": "Closed trades only; account units are not necessarily USD. No profit claims.",
    }


def atomic_publish(path, report):
    dest = Path(path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".v2-mt5-", suffix=".tmp", dir=dest.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(report, stream, ensure_ascii=False, allow_nan=False, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, dest)
    finally:
        Path(temporary).unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="/data/ramon_history.sqlite3")
    parser.add_argument("--output", default="/data/v2_mt5_execution_health.json")
    parser.add_argument("--symbol", default="XAUUSD_l")
    parser.add_argument("--limit", type=int, default=500)
    parser.add_argument("--interval", type=int, default=30)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    if args.interval < 10:
        parser.error("interval must be at least 10 seconds")
    while True:
        report = analyze(args.db, symbol=args.symbol, limit=args.limit)
        report["generated_utc"] = datetime.now(timezone.utc).isoformat()
        atomic_publish(args.output, report)
        print(json.dumps({"ready": report["ready"], "trades": report.get("trade_count", 0),
                          "warnings": report.get("quality_warnings", [])}), flush=True)
        if args.once:
            return
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
