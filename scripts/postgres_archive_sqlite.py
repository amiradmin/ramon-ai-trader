#!/usr/bin/env python3
"""Copy legacy SQLite tables to a separate PostgreSQL archive schema.

This is an additive, resumable archive migration, NOT a live database cutover.
No SQLite writes, no mutations of existing public.* PostgreSQL tables.
Run only after taking verified backups and checking available disk space.
"""
from __future__ import annotations

import argparse
import base64
import csv
import io
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

SKIP = {"decision_samples", "trade_outcomes"}
DEFAULT_CONTAINER = "ramon-ai-trader-postgres-logs-1"


def pg(container: str, sql: str, *, input_data: str | None = None) -> str:
    cmd = [
        "docker", "exec", "-i", container, "sh", "-c",
        'exec psql -X -q -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"',
    ]
    p = subprocess.run(cmd, input=sql if input_data is None else input_data,
                       text=True, capture_output=True)
    if p.returncode != 0:
        raise RuntimeError(f"psql failed: {p.stderr.strip()}")
    return p.stdout


def json_safe(value):
    if isinstance(value, bytes):
        return {"__sqlite_blob_base64__": base64.b64encode(value).decode("ascii")}
    return value


def table_names(db: sqlite3.Connection) -> list[str]:
    return [r[0] for r in db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    )]


def qident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def migrate_table(db: sqlite3.Connection, container: str, name: str, batch: int) -> int:
    cols = [r[1] for r in db.execute(f"PRAGMA table_info({qident(name)})")]
    if not cols:
        return 0
    # Save a consistent snapshot for each table. Retain original SQLite rowid
    # alongside values to reconstruct tables lacking an explicit PK.
    use_rowid = True
    try:
        cursor = db.execute(f"SELECT rowid,* FROM {qident(name)} ORDER BY rowid")
    except sqlite3.OperationalError:
        use_rowid = False
        cursor = db.execute(f"SELECT * FROM {qident(name)}")
    moved = 0
    while True:
        rows = cursor.fetchmany(batch)
        if not rows:
            break
        buf = io.StringIO()
        writer = csv.writer(buf, lineterminator="\n")
        for row in rows:
            rowid = str(row[0]) if use_rowid else str(moved)
            values = row[1:] if use_rowid else row
            payload = {key: json_safe(val) for key, val in zip(cols, values)}
            writer.writerow([name, rowid, json.dumps(payload, ensure_ascii=False, allow_nan=False)])
            moved += 1
        # Entire batch is atomic, idempotent on (source_table, source_rowid).
        script = (
            "BEGIN;\n"
            "CREATE TEMP TABLE ingest_batch (source_table text, source_rowid text, payload jsonb) ON COMMIT DROP;\n"
            "COPY ingest_batch (source_table, source_rowid, payload) FROM STDIN WITH (FORMAT csv);\n"
            + buf.getvalue()
            + "\\.\n"
            "INSERT INTO sqlite_archive.rows (source_table,source_rowid,payload) "
            "SELECT source_table,source_rowid,payload FROM ingest_batch "
            "ON CONFLICT (source_table,source_rowid) DO UPDATE "
            "SET payload=EXCLUDED.payload, archived_at=now();\n"
            "COMMIT;\n"
        )
        pg(container, script)
    return moved


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sqlite", default="data/ramon_history.sqlite3")
    ap.add_argument("--container", default=DEFAULT_CONTAINER)
    ap.add_argument("--batch", type=int, default=500)
    ap.add_argument("--include", nargs="*", help="Only archive selected legacy tables")
    ap.add_argument("--execute", action="store_true", help="Write archive rows; default is dry run")
    args = ap.parse_args()
    if args.batch < 1 or args.batch > 5000:
        ap.error("--batch must be 1..5000")
    path = Path(args.sqlite).resolve()
    if not path.is_file():
        ap.error(f"SQLite not found: {path}")
    db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=30)
    try:
        db.execute("PRAGMA query_only=ON")
        tables = [t for t in table_names(db) if t not in SKIP]
        if args.include is not None:
            bad = set(args.include) - set(tables)
            if bad:
                ap.error("Unrecognized or excluded table(s): " + ", ".join(sorted(bad)))
            tables = [t for t in tables if t in args.include]
        counts = {t: db.execute(f"SELECT COUNT(*) FROM {qident(t)}").fetchone()[0] for t in tables}
        print(json.dumps({"mode": "archive" if args.execute else "dry-run",
                          "tables": counts, "excluded_live_mapped_tables": sorted(SKIP)},
                         ensure_ascii=False), flush=True)
        if not args.execute:
            return 0
        pg(args.container, """
CREATE SCHEMA IF NOT EXISTS sqlite_archive;
CREATE TABLE IF NOT EXISTS sqlite_archive.rows (
    source_table text NOT NULL,
    source_rowid text NOT NULL,
    payload jsonb NOT NULL,
    archived_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(source_table,source_rowid)
);
""")
        # A live SQLite writer may add rows after this snapshot; rerun to copy
        # new rows. Never treat archive completion as approval to delete SQLite.
        for t in tables:
            n = migrate_table(db, args.container, t, args.batch)
            print(f"archived {t}: {n}/{counts[t]}", flush=True)
        print("ARCHIVE COMPLETE FOR SELECTED SNAPSHOT; CUTOVER NOT VERIFIED", flush=True)
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (sqlite3.Error, OSError, RuntimeError, ValueError) as exc:
        print(f"ARCHIVE ERROR: {exc}", file=sys.stderr)
        sys.exit(3)
