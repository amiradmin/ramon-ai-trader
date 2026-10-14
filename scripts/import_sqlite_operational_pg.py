#!/usr/bin/env python3
"""Populate native ramon.* tables from an existing SQLite snapshot.

Every SQLite table is already copied losslessly to sqlite_archive.rows by
migrate_data_first.sh. This extra step populates typed operational tables.
A failed table is reported explicitly; never claim full cutover on partial copy.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from psycopg import connect
from psycopg import sql
from psycopg.types.json import Jsonb

from ramon.postgres_operational import DDL

TABLES = (
    "history_bars", "mtf_utc_bars", "inference_audit", "human_market_opinions",
    "manual_gate_overrides", "target_outcomes", "shadow_votes",
    "decision_samples", "trade_outcomes",
)
JSON_COLS = {"regime_features", "entry_features", "meta_base_features",
             "news_features", "metadata", "telemetry", "snapshot",
             "payload", "response_json", "provenance_json", "input_context"}
# These two are retained completely in ramon_migration.* in their original JSON form;
# typed tables are best-effort because historical schemas may differ.
CRITICAL = {"history_bars", "mtf_utc_bars"}


def migrate(path: Path, dsn: str, batch_size: int = 500):
    if not 1 <= batch_size <= 5000:
        raise ValueError("batch must be 1..5000")
    result = {}
    with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as src:
        src.row_factory = sqlite3.Row
        src.execute("PRAGMA query_only=ON")
        with connect(dsn, connect_timeout=5) as target:
            with target.cursor() as cur:
                cur.execute(DDL)
            target.commit()
            for table in TABLES:
                try:
                    columns = [r[1] for r in src.execute(
                        f'PRAGMA table_info("{table}")')]
                    if not columns:
                        result[table] = {"status": "not_in_source"}
                        continue
                    with target.cursor() as cur:
                        cur.execute("""SELECT column_name, is_identity, data_type
                          FROM information_schema.columns
                          WHERE table_schema='ramon' AND table_name=%s
                          ORDER BY ordinal_position""", (table,))
                        dest_columns = cur.fetchall()
                    # Older SQLite versions did not include required JSON payload
                    # columns. Restore the missing values from existing archived rows
                    # when available; otherwise supply explicit empty JSON objects.
                    names = [n for n, identity, _ in dest_columns
                             if n in columns and identity == "NO"]
                    required_json = {
                        "inference_audit": "input_context",
                        "human_market_opinions": "snapshot",
                        "manual_gate_overrides": "payload",
                    }.get(table)
                    if required_json and required_json not in names:
                        names.append(required_json)
                    if not names:
                        result[table] = {"status": "no_mapped_columns"}
                        continue
                    primary = {
                        "history_bars": ("symbol", "timeframe", "time"),
                        "mtf_utc_bars": ("symbol", "timeframe", "time"),
                        "inference_audit": ("sample_key",),
                        "target_outcomes": ("sample_key",),
                        "shadow_votes": ("vote_key",),
                        "decision_samples": ("sample_key",),
                        "trade_outcomes": ("trade_key",),
                    }.get(table)
                    command = sql.SQL("INSERT INTO {} ({}) VALUES ({}) {}").format(
                        sql.Identifier("ramon", table),
                        sql.SQL(",").join(map(sql.Identifier, names)),
                        sql.SQL(",").join(sql.Placeholder() for _ in names),
                        sql.SQL("ON CONFLICT DO NOTHING") if primary else sql.SQL("")
                    )
                    source_names = [name for name in names if name in columns]
                    query = 'SELECT ' + ",".join('"' + x + '"' for x in source_names) + f' FROM "{table}"'
                    imported = 0
                    with target.cursor() as cur:
                        rows = src.execute(query)
                        while batch := rows.fetchmany(batch_size):
                            prepared = []
                            for row in rows_to_dicts(batch):
                                values = []
                                for name in names:
                                    value = row.get(name)
                                    if name == required_json and value is None:
                                        # Keep provenance instead of inventing an
                                        # original snapshot/override payload.
                                        value = {
                                            "_migration_missing_field": name,
                                            "_legacy_original_row": row,
                                        }
                                    if name in JSON_COLS and value is not None:
                                        if isinstance(value, str):
                                            value = json.loads(value)
                                        value = Jsonb(value)
                                    values.append(value)
                                prepared.append(tuple(values))
                            cur.executemany(command, prepared)
                            imported += len(prepared)
                    target.commit()
                    result[table] = {"status": "copied", "source_rows": imported}
                except Exception as exc:
                    target.rollback()
                    result[table] = {"status": "failed", "type": type(exc).__name__,
                                     "detail": str(exc)[:280]}
    return result


def rows_to_dicts(rows):
    for row in rows:
        yield dict(row)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--sqlite", type=Path, required=True)
    p.add_argument("--password-file", type=Path, required=True)
    p.add_argument("--host", default="postgres-logs")
    args = p.parse_args()
    from psycopg.conninfo import make_conninfo
    dsn = make_conninfo(host=args.host, port=5432, dbname="ramon_logs",
                        user="ramon_logger", password=args.password_file.read_text().strip())
    result = migrate(args.sqlite, dsn)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    failed = [name for name, item in result.items() if item.get("status") == "failed"]
    if failed:
        raise SystemExit("INCOMPLETE: failed tables: " + ", ".join(failed))


if __name__ == "__main__":
    main()
