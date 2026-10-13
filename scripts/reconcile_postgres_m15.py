#!/usr/bin/env python3
"""Reconcile completed M15 market bars from authoritative SQLite to PostgreSQL.

Read-only SQLite; PostgreSQL upsert is explicit --execute. Designed for
recovering lost best-effort asynchronous shadow writes. Does not cut over Ramon.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path


UPSERT = """INSERT INTO ramon_migration.history_bars
 (symbol,timeframe,time,open,high,low,close,spread_points)
 VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
 ON CONFLICT (symbol,timeframe,time) DO UPDATE SET
 open=EXCLUDED.open, high=EXCLUDED.high, low=EXCLUDED.low,
 close=EXCLUDED.close,
 spread_points=CASE WHEN EXCLUDED.spread_points>0
 THEN EXCLUDED.spread_points ELSE ramon_migration.history_bars.spread_points END"""


def reconcile(sqlite_path: Path, dsn: str, symbol: str, *, execute: bool,
              batch_size: int = 500) -> dict:
    if not sqlite_path.is_file():
        raise FileNotFoundError(sqlite_path)
    if batch_size < 1 or batch_size > 5000:
        raise ValueError("batch size must be 1..5000")
    with sqlite3.connect(sqlite_path.resolve().as_uri() + "?mode=ro", uri=True) as source:
        source.execute("PRAGMA query_only=ON")
        source.execute("BEGIN")  # consistent point-in-time SQLite snapshot
        count = source.execute(
            "SELECT COUNT(*) FROM history_bars WHERE symbol=? AND timeframe='M15'",
            (symbol,),
        ).fetchone()[0]
        if not execute:
            return {"status": "DRY_RUN", "source_rows": count, "written": 0,
                    "safe_to_delete_sqlite": False}
        if not dsn:
            raise ValueError("PostgreSQL DSN required for --execute")
        import psycopg
        written = 0
        with psycopg.connect(dsn, connect_timeout=5) as target:
            with target.cursor() as cur:
                read = source.execute(
                    """SELECT symbol,timeframe,time,open,high,low,close,
                              COALESCE(spread_points,0)
                       FROM history_bars WHERE symbol=? AND timeframe='M15'
                       ORDER BY time""", (symbol,))
                while True:
                    rows = read.fetchmany(batch_size)
                    if not rows:
                        break
                    cur.executemany(UPSERT, rows)
                    written += len(rows)
    return {"status": "RECONCILED", "source_rows": count,
            "written": written, "safe_to_delete_sqlite": False}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sqlite", type=Path, default=Path("data/ramon_history.sqlite3"))
    ap.add_argument("--dsn", default="", help="Connection string (prefer secret environment expansion)")
    ap.add_argument("--symbol", default="XAUUSD_l")
    ap.add_argument("--batch-size", type=int, default=500)
    ap.add_argument("--execute", action="store_true")
    args = ap.parse_args()
    try:
        out = reconcile(args.sqlite, args.dsn, args.symbol,
                        execute=args.execute, batch_size=args.batch_size)
    except Exception as exc:
        # No credentials or DSNs in logs.
        print(json.dumps({"status": "ERROR", "error_type": type(exc).__name__,
                          "safe_to_delete_sqlite": False}))
        return 3
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
