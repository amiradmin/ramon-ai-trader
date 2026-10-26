#!/usr/bin/env python3
"""Read-only, exact M15 SQLite vs PostgreSQL comparison; never a cutover gate alone."""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

from ramon.postgres_history import load_bars_postgres


def compare(sqlite_path: Path, pg_dsn: str, symbol: str) -> dict:
    if not sqlite_path.is_file():
        raise FileNotFoundError(sqlite_path)
    with sqlite3.connect(sqlite_path.resolve().as_uri() + "?mode=ro", uri=True) as con:
        con.execute("PRAGMA query_only=ON")
        local = con.execute(
            """SELECT time,open,high,low,close,COALESCE(spread_points,0)
               FROM history_bars WHERE symbol=? AND timeframe='M15' ORDER BY time""",
            (symbol,),
        ).fetchall()
    bars, spreads = load_bars_postgres(pg_dsn, symbol)
    remote = [(bar.time, bar.open, bar.high, bar.low, bar.close, spread)
              for bar, spread in zip(bars, spreads)]
    left = {int(row[0]): row[1:] for row in local}
    right = {int(row[0]): row[1:] for row in remote}
    missing_pg = sorted(left.keys() - right.keys())
    extra_pg = sorted(right.keys() - left.keys())
    differences = [t for t in left.keys() & right.keys() if left[t] != right[t]]
    return {
        "symbol": symbol,
        "sqlite_count": len(local),
        "postgres_count": len(remote),
        "missing_in_postgres_count": len(missing_pg),
        "extra_in_postgres_count": len(extra_pg),
        "field_mismatch_count": len(differences),
        "missing_in_postgres_sample": missing_pg[:10],
        "extra_in_postgres_sample": extra_pg[:10],
        "field_mismatch_sample": sorted(differences)[:10],
        "last_sqlite_time": max(left) if left else None,
        "last_postgres_time": max(right) if right else None,
        "status": "PARITY" if left == right and local and remote else "NOT_PARITY",
        "safe_to_delete_sqlite": False,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sqlite", type=Path, default=Path("data/ramon_history.sqlite3"))
    ap.add_argument("--dsn", required=True, help="PostgreSQL DSN; use environment expansion, never commit secrets")
    ap.add_argument("--symbol", default="XAUUSD_l")
    args = ap.parse_args()
    try:
        result = compare(args.sqlite, args.dsn, args.symbol)
    except Exception as exc:
        # Avoid printing connection exceptions that might include credentials.
        print(json.dumps({"status": "ERROR", "error_type": type(exc).__name__,
                          "safe_to_delete_sqlite": False}))
        return 3
    print(json.dumps(result, indent=2))
    return 0 if result["status"] == "PARITY" else 2


if __name__ == "__main__":
    sys.exit(main())
