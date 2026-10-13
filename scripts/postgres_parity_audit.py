#!/usr/bin/env python3
"""Read-only SQLite / PostgreSQL migration gate for Ramon.

Runs on the Ubuntu host. PostgreSQL access is via docker exec / psql,
so no password is read or printed. No changes are made to either DB.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

PAIRS = [
    ("decision_samples", "sample_key", "decision_features", "sample_key"),
    ("trade_outcomes", "trade_key", "trade_labels", "trade_key"),
]
PG_TABLES = [
    "decision_features", "trade_labels", "raw_research_events",
    "position_quotes", "position_actions", "ingest_cursors", "ingest_errors",
]


def postgres(container: str, sql: str) -> list[str]:
    cmd = [
        "docker", "exec", "-i", "-e", "PGOPTIONS=-c default_transaction_read_only=on",
        container, "sh", "-c",
        'exec psql -X -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -At',
    ]
    p = subprocess.run(cmd, input=sql, text=True, capture_output=True, check=False)
    if p.returncode:
        raise RuntimeError(f"PostgreSQL query failed (exit {p.returncode}): {p.stderr.strip()}")
    lines = p.stdout.splitlines()
    if not lines:
        raise RuntimeError("PostgreSQL returned no rows; verify stdin/connection and query")
    return lines


def sqlite_tables(db: sqlite3.Connection) -> dict[str, int]:
    names = [r[0] for r in db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    )]
    return {name: db.execute(f'SELECT count(*) FROM "{name}"').fetchone()[0] for name in names}


def pg_counts(container: str) -> dict[str, int]:
    rows = postgres(container, """
        SELECT tablename FROM pg_tables
        WHERE schemaname='public' ORDER BY tablename;
    """)
    result = {}
    for name in rows:
        if not name.replace("_", "").isalnum():
            raise RuntimeError(f"Unexpected PostgreSQL table: {name!r}")
        result[name] = int(postgres(container, f'SELECT count(*) FROM public."{name}";')[0])
    return result


def key_coverage(db: sqlite3.Connection, container: str, st: str, sk: str,
                 pt: str, pk: str) -> dict:
    sqlite_keys = {str(x[0]) for x in db.execute(
        f'SELECT "{sk}" FROM "{st}" WHERE "{sk}" IS NOT NULL AND trim("{sk}") <> ""'
    )}
    pg_keys = set(postgres(container, f"""
        SELECT "{pk}"::text FROM public."{pt}"
        WHERE "{pk}" IS NOT NULL AND "{pk}"::text <> '';
    """))
    missing = sqlite_keys - pg_keys
    return {
        "sqlite_nonempty_distinct_keys": len(sqlite_keys),
        "postgres_nonempty_distinct_keys": len(pg_keys),
        "missing_in_postgres_count": len(missing),
        "missing_in_postgres_examples": sorted(missing)[:10],
        "only_in_postgres_count": len(pg_keys - sqlite_keys),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sqlite", default="data/ramon_history.sqlite3")
    ap.add_argument("--container", default="ramon-ai-trader-postgres-logs-1")
    ap.add_argument("--json", action="store_true", help="Emit JSON report")
    args = ap.parse_args()
    path = Path(args.sqlite).resolve()
    if not path.is_file():
        ap.error(f"SQLite file not found: {path}")
    db = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
    try:
        db.execute("PRAGMA query_only=ON")
        local = sqlite_tables(db)
        remote = pg_counts(args.container)
        coverage = {}
        for st, sk, pt, pk in PAIRS:
            if st in local and pt in remote:
                coverage[f"{st}.{sk} -> {pt}.{pk}"] = key_coverage(
                    db, args.container, st, sk, pt, pk
                )
        errors = postgres(args.container,
                          "SELECT count(*) FROM public.ingest_errors;")
        unmapped = sorted(set(local) - {pair[0] for pair in PAIRS})
        report = {
            "result": "NOT_SAFE_TO_DELETE",
            "sqlite_path": str(path),
            "postgres_container": args.container,
            "sqlite_tables": local,
            "postgres_tables": remote,
            "key_coverage": coverage,
            "sqlite_tables_without_verified_migration": unmapped,
            "ingest_errors": int(errors[0]),
            "note": "Key coverage does not prove payload parity or replacement of running SQLite consumers.",
        }
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            print("DELETE GATE: BLOCKED — SQLite consumers and unmapped tables remain.")
            print("SQLite tables:", json.dumps(local, ensure_ascii=False))
            print("PostgreSQL tables:", json.dumps(remote, ensure_ascii=False))
            print("Key coverage:", json.dumps(coverage, ensure_ascii=False, indent=2))
            print("Unmapped SQLite tables:", ", ".join(unmapped))
            print("Ingest errors:", errors[0])
        return 2
    finally:
        db.close()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, sqlite3.Error, RuntimeError, ValueError) as exc:
        print(f"AUDIT FAILED: {exc}", file=sys.stderr)
        sys.exit(3)
