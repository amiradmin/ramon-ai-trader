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
    """Read training examples natively from PostgreSQL, without SQLite.

    Filtering and ordering mirror train_roles.read_trade_examples. The
    training label is kept separate from features to prevent target leakage.
    """
    import psycopg
    from .train_roles import Example

    query = """
        SELECT s.source, t.source
        FROM ramon_migration.training_decision_samples s
        JOIN ramon_migration.training_trade_outcomes t
          ON t.source->>'sample_key' = s.source->>'sample_key'
        WHERE s.source->>'symbol' = %s
          AND t.source->>'symbol' = s.source->>'symbol'
          AND s.source->>'chronos_model' = %s
          AND (s.source->>'schema_version')::integer >= 3
          AND s.source->>'news_features' IS NOT NULL
          AND t.source->>'direction' = s.source->>'direction'
          AND t.source->>'training_status' = 'LEARNABLE'
          AND (
              s.source->>'final_decision' = t.source->>'direction'
              OR COALESCE(t.source->>'entry_source', 'AUTO_RAMON') = 'DASHBOARD_OPPORTUNITY'
          )
          AND (t.source->>'opened')::bigint >= (s.source->>'quote_time')::bigint
          AND (t.source->>'opened')::bigint <= (s.source->>'quote_time')::bigint + 90
          AND (t.source->>'closed')::bigint >= (t.source->>'opened')::bigint
        ORDER BY (s.source->>'quote_time')::bigint,
                 s.source->>'sample_key',
                 t.source->>'trade_key'
    """
    with psycopg.connect(dsn, connect_timeout=5,
                         options="-c statement_timeout=30000") as pg:
        with pg.cursor() as cur:
            cur.execute(query, (symbol, chronos_model))
            rows = cur.fetchall()

    def features(value):
        return json.loads(value) if isinstance(value, str) else value

    return [
        Example(
            int(s["quote_time"]),
            int(t["closed"]),
            {
                "regime": features(s["regime_features"]),
                "entry": features(s["entry_features"]),
                "news": features(s["news_features"]),
                "meta_base": features(s["meta_base_features"]),
            },
            int(float(t["net_r"]) > 0),
            float(t["net_r"]),
            s.get("base_decision") in {"BUY", "SELL"},
            t["direction"],
            t["exit_reason"],
        )
        for s, t in rows
    ]
