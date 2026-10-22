"""PostgreSQL-backed training samples and outcomes.

The source rows are retained losslessly as JSONB during the migration, but
the trainer selection is executed natively in PostgreSQL.  This module never
opens a SQLite database while reading training examples.
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

# Keep this predicate semantically identical to ``train_roles.read_trade_examples``.
# JSONB fields are cast only after the migration writer has stored them; malformed
# records are a migration failure rather than a silent, differently-labelled row.
TRAINING_EXAMPLES_SQL = """
    SELECT
        (s.source->>'quote_time')::bigint,
        (t.source->>'closed')::bigint,
        s.source->>'regime_features',
        s.source->>'entry_features',
        s.source->>'news_features',
        s.source->>'meta_base_features',
        (t.source->>'net_r')::double precision,
        s.source->>'base_decision',
        t.source->>'direction',
        t.source->>'exit_reason'
    FROM ramon_migration.training_decision_samples AS s
    JOIN ramon_migration.training_trade_outcomes AS t
      ON t.source->>'sample_key' = s.sample_key
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
    ORDER BY
        (s.source->>'quote_time')::bigint,
        s.source->>'sample_key',
        t.source->>'trade_key'
"""


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
    """Read filtered trainer examples directly from PostgreSQL.

    The explicit read-only transaction prevents accidental writes from a
    training process and gives a single consistent snapshot of both tables.
    """
    import psycopg
    from .train_roles import Example
    with psycopg.connect(dsn, connect_timeout=5,
                         options="-c statement_timeout=30000") as pg:
        with pg.cursor() as cur:
            cur.execute("BEGIN TRANSACTION READ ONLY")
            cur.execute(TRAINING_EXAMPLES_SQL, (symbol, chronos_model))
            rows = cur.fetchall()
    return [
        Example(
            int(quote_time), int(closed),
            {
                "regime": json.loads(regime),
                "entry": json.loads(entry),
                "news": json.loads(news),
                "meta_base": json.loads(meta_base),
            },
            int(float(net_r) > 0), float(net_r), base in {"BUY", "SELL"},
            direction, exit_reason,
        )
        for quote_time, closed, regime, entry, news, meta_base, net_r,
        base, direction, exit_reason in rows
    ]


def dashboard_training_stats(dsn: str, symbol: str, chronos_model: str):
    """Read dashboard aggregates and recent trades from PostgreSQL directly."""
    import psycopg
    where = """
        s.source->>'symbol' = %s
        AND t.source->>'symbol' = s.source->>'symbol'
        AND s.source->>'chronos_model' = %s
        AND (s.source->>'schema_version')::integer >= 3
        AND s.source->>'news_features' IS NOT NULL
        AND t.source->>'direction' = s.source->>'direction'
        AND t.source->>'training_status' = 'LEARNABLE'
        AND t.source->>'entry_source' = 'DASHBOARD_OPPORTUNITY'
        AND (t.source->>'opened')::bigint >= (s.source->>'quote_time')::bigint
        AND (t.source->>'opened')::bigint <= (s.source->>'quote_time')::bigint + 90
        AND (t.source->>'closed')::bigint >= (t.source->>'opened')::bigint
    """
    with psycopg.connect(dsn, connect_timeout=5, options="-c statement_timeout=30000") as pg:
        with pg.cursor() as cur:
            cur.execute(f"""
                SELECT COUNT(*),
                       COUNT(*) FILTER (WHERE (t.source->>'net_units')::double precision > 0),
                       COUNT(*) FILTER (WHERE (t.source->>'net_units')::double precision <= 0),
                       COALESCE(SUM((t.source->>'net_units')::double precision), 0)
                FROM ramon_migration.training_decision_samples s
                JOIN ramon_migration.training_trade_outcomes t
                  ON t.source->>'sample_key' = s.sample_key
                WHERE {where}
            """, (symbol, chronos_model))
            n, wins, losses, net = cur.fetchone()
            cur.execute("""
                SELECT to_char(to_timestamp((source->>'closed')::bigint) AT TIME ZONE 'UTC',
                               'YYYY-MM-DD HH24:MI:SS'),
                       source->>'direction', (source->>'net_units')::double precision,
                       source->>'entry_source', source->>'training_status'
                FROM ramon_migration.training_trade_outcomes
                WHERE source->>'symbol' = %s
                  AND source->>'entry_source' = 'DASHBOARD_OPPORTUNITY'
                ORDER BY (source->>'closed')::bigint DESC, source->>'trade_key' DESC LIMIT 30
            """, (symbol,))
            recent = [
                dict(zip(("closed_utc", "direction", "net_units", "entry_source", "training_status"), row))
                for row in cur.fetchall()
            ]
    return {
        "dashboard_trainable": int(n),
        "dashboard_wins": int(wins),
        "dashboard_losses": int(losses),
        "dashboard_net_units": round(float(net), 4),
    }, recent
