"""PostgreSQL-backed training samples and outcomes, with exact SQLite trainer semantics.

Staging storage uses JSONB to keep lossless rows, and reconstructs the
existing SQLite query in memory until the training SQL is fully ported.
Never connects to the live trading server.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

DDL = """
CREATE SCHEMA IF NOT EXISTS ramon_migration;
CREATE TABLE IF NOT EXISTS ramon_migration.training_decision_samples (
    sample_key text PRIMARY KEY, source jsonb NOT NULL
);
CREATE TABLE IF NOT EXISTS ramon_migration.training_trade_outcomes (
    trade_key text PRIMARY KEY, source jsonb NOT NULL
);
"""
DECISION_COLS = ("sample_key","symbol","quote_time","regime_features",
                 "entry_features","news_features","meta_base_features",
                 "base_decision","direction","chronos_model",
                 "schema_version","final_decision")
TRADE_COLS = ("trade_key","sample_key","symbol","closed","net_r",
              "direction","exit_reason","training_status","entry_source","opened")


def sync_training_tables(sqlite_file: Path, dsn: str, batch: int = 500) -> dict[str, int]:
    """Incremental key-based idempotent upsert from an SQLite read snapshot."""
    if not 1 <= batch <= 5000:
        raise ValueError("batch must be between 1 and 5000")
    import psycopg
    totals: dict[str, int] = {}
    with sqlite3.connect(sqlite_file.resolve().as_uri() + "?mode=ro", uri=True) as source:
        source.execute("BEGIN")
        with psycopg.connect(dsn, connect_timeout=5) as pg:
            with pg.cursor() as cur:
                cur.execute(DDL)
                for table, target, key in (
                    ("decision_samples", "training_decision_samples", "sample_key"),
                    ("trade_outcomes", "training_trade_outcomes", "trade_key"),
                ):
                    cursor = source.execute(f"SELECT * FROM {table} WHERE {key} IS NOT NULL")
                    columns = [c[0] for c in cursor.description]
                    totals[table] = 0
                    while rows := cursor.fetchmany(batch):
                        payloads = []
                        for row in rows:
                            record = dict(zip(columns, row))
                            payloads.append((str(record[key]), json.dumps(record, allow_nan=False)))
                        cur.executemany(
                            f"""INSERT INTO ramon_migration.{target} ( {key}, source )
                                VALUES (%s,%s::jsonb)
                                ON CONFLICT ({key}) DO UPDATE SET source=EXCLUDED.source\n                                WHERE ramon_migration.{target}.source IS DISTINCT FROM EXCLUDED.source""",
                            payloads,
                        )
                        totals[table] += len(rows)
    return totals


def read_training_examples(dsn: str, symbol: str, chronos_model: str):
    """Preserve the trainer's filters exactly while PostgreSQL holds the source."""
    import psycopg
    from .train_roles import read_trade_examples
    with psycopg.connect(dsn, connect_timeout=5,
                         options="-c statement_timeout=30000") as pg:
        with pg.cursor() as cur:
            cur.execute("SELECT source FROM ramon_migration.training_decision_samples WHERE source->>'symbol'=%s",
                        (symbol,))
            samples = [v[0] for v in cur.fetchall()]
            cur.execute("SELECT source FROM ramon_migration.training_trade_outcomes WHERE source->>'symbol'=%s",
                        (symbol,))
            outcomes = [v[0] for v in cur.fetchall()]
    # Minimal in-memory compatibility layer; does not access a SQLite file.
    with sqlite3.connect(":memory:") as db:
        numeric = {"quote_time": "INTEGER", "schema_version": "INTEGER",
                   "opened": "INTEGER", "closed": "INTEGER", "net_r": "REAL"}
        db.execute("CREATE TABLE decision_samples (" +
                   ",".join(f"{name} {numeric.get(name, 'TEXT')}" for name in DECISION_COLS) + ")")
        db.execute("CREATE TABLE trade_outcomes (" +
                   ",".join(f"{name} {numeric.get(name, 'TEXT')}" for name in TRADE_COLS) + ")")
        for table, columns, records in (
            ("decision_samples", DECISION_COLS, samples),
            ("trade_outcomes", TRADE_COLS, outcomes),
        ):
            stmt = f"INSERT INTO {table} VALUES (" + ",".join("?" for _ in columns) + ")"
            db.executemany(stmt, [
                tuple(record.get(col) if not isinstance(record.get(col), (dict,list))
                      else json.dumps(record[col]) for col in columns)
                for record in records
            ])
        return read_trade_examples(db, symbol, chronos_model)
