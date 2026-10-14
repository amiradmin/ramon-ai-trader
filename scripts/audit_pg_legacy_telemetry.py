#!/usr/bin/env python3
"""Read-only per-trade forensic audit of missing PostgreSQL telemetry.

Never edits SQLite, PostgreSQL, positions, or migration status. Only exact
trade_key matches are compared; sample_key collisions remain unresolved.
"""
import argparse
import json
import os
import sqlite3
from collections import Counter
from pathlib import Path

FIELDS = ("entry_ea_version", "entry_source", "trade_role", "exit_detail",
          "closed_utc_offset_seconds", "risk_budget_units",
          "min_lot_override_used", "max_executable_risk_usd")


def compare(src_rows, pg_rows):
    stats = {key: Counter() for key in FIELDS}
    conflicts = []
    for key, source in src_rows.items():
        target = pg_rows.get(key)
        if target is None:
            continue
        telemetry = target.get("telemetry") or {}
        for field in FIELDS:
            source_value = source.get(field)
            target_value = telemetry.get(field)
            if source_value is None:
                stats[field]["sqlite_null"] += 1
            elif target_value is None:
                stats[field]["recoverable_if_verified"] += 1
            elif str(source_value) == str(target_value):
                stats[field]["equal"] += 1
            else:
                stats[field]["conflict"] += 1
                if len(conflicts) < 15:
                    conflicts.append({"trade_key": key, "field": field,
                                      "sqlite": source_value, "postgres": target_value})
    return {k: dict(v) for k, v in stats.items()}, conflicts


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sqlite", type=Path, default=Path("/data/ramon_history.sqlite3"))
    parser.add_argument("--symbol", default="XAUUSD_l")
    args = parser.parse_args()
    if not args.sqlite.is_file():
        parser.error("SQLite archive file not present")
    dsn = os.getenv("RAMON_PG_DSN")
    if not dsn:
        parser.error("RAMON_PG_DSN is not configured")
    with sqlite3.connect(args.sqlite.resolve().as_uri() + "?mode=ro", uri=True) as src:
        src.row_factory = sqlite3.Row
        src.execute("PRAGMA query_only=ON")
        columns = {row[1] for row in src.execute("PRAGMA table_info(trade_outcomes)")}
        present = [name for name in FIELDS if name in columns]
        selection = ", ".join(["trade_key", "sample_key", *present])
        src_rows = {r["trade_key"]: dict(r) for r in
                    src.execute(f"SELECT {selection} FROM trade_outcomes WHERE symbol=?", (args.symbol,))}
        for row in src_rows.values():
            for field in FIELDS:
                row.setdefault(field, None)
    import psycopg
    from psycopg.rows import dict_row
    with psycopg.connect(dsn, connect_timeout=5,
                         options="-c default_transaction_read_only=on -c statement_timeout=12000",
                         row_factory=dict_row) as con:
        with con.cursor() as cur:
            cur.execute("SELECT trade_key,sample_key,telemetry FROM ramon.trade_outcomes WHERE symbol=%s", (args.symbol,))
            pg_rows = {r["trade_key"]: r for r in cur.fetchall()}
    shared = src_rows.keys() & pg_rows.keys()
    sample_mismatch = sum(src_rows[k]["sample_key"] != pg_rows[k]["sample_key"] for k in shared)
    stats, conflicts = compare(src_rows, pg_rows)
    print(json.dumps({
        "mode": "READ_ONLY", "symbol": args.symbol,
        "sqlite_rows": len(src_rows), "pg_rows": len(pg_rows),
        "matched_trade_keys": len(shared),
        "sqlite_only_trade_keys": len(src_rows.keys() - pg_rows.keys()),
        "pg_only_trade_keys": len(pg_rows.keys() - src_rows.keys()),
        "sample_key_mismatches_on_shared_trades": sample_mismatch,
        "field_comparison": stats, "conflict_examples": conflicts,
        "action": "AUDIT_ONLY_NO_BACKFILL",
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
