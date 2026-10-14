"""Read-only PostgreSQL adapter for the existing opportunity projection.

Queries native MTF bars and losslessly migrated decision JSON; never changes
trading permissions. Deliberately fails closed when required data is absent.
"""
import os
import sqlite3
import json
from psycopg import connect


class _Row:
    def __init__(self, names, values):
        self.names = names
        self.values = values
    def __getitem__(self, key):
        return self.values[self.names.index(key)] if isinstance(key, str) else self.values[key]


class _Cursor:
    def __init__(self, names, rows):
        self.rows = [_Row(names, r) for r in rows]
    def fetchone(self):
        return self.rows[0] if self.rows else None
    def fetchall(self):
        return list(self.rows)
    def __iter__(self):
        return iter(self.rows)


class OpportunityPG:
    def __init__(self):
        dsn = os.environ["RAMON_PG_DSN"]
        self.conn = connect(dsn, connect_timeout=4,
            options="-c default_transaction_read_only=on -c statement_timeout=8000")
    def close(self):
        self.conn.close()
    def execute(self, query, params=()):
        q = " ".join(query.split()).lower()
        args = tuple(params)
        if "sqlite_master" in q:
            return _Cursor(["name"], [("mtf_utc_bars",), ("decision_samples",), ("trade_outcomes",)])
        if "from mtf_utc_bars" in q:
            table = "ramon.mtf_utc_bars"
            sql = query.replace("mtf_utc_bars", table).replace("?", "%s")
        elif "from decision_samples" in q:
            # Live decisions are written to the native operational table.
            # The migration archive is intentionally NOT the live source.
            table = "ramon.decision_samples"
            if "max(captured)" in q:
                sql = "SELECT max(captured) FROM "+table+" WHERE symbol=%s"
            else:
                sql = """SELECT captured, signal_bar_time, sample_key,
                    quote_time, mid, spread,
                    (metadata->'model_metadata'->'decision_audit'->'base'->>'stop_distance')::double precision AS stop_distance,
                    (metadata->'model_metadata'->'decision_audit'->'base'->>'target_distance')::double precision AS target_distance,
                    final_decision,
                    COALESCE(metadata->'model_metadata','{}'::jsonb)::text AS model_metadata
                    FROM """+table+""" WHERE symbol=%s AND captured >= %s
                    ORDER BY captured, sample_key"""
        elif "from trade_outcomes" in q:
            sql = "SELECT sample_key FROM ramon.trade_outcomes WHERE symbol=%s"
        else:
            raise sqlite3.OperationalError("Unsupported PostgreSQL opportunity query")
        try:
            with self.conn.cursor() as cur:
                cur.execute(sql, args)
                names = [c.name for c in cur.description]
                return _Cursor(names, cur.fetchall())
        except Exception as exc:
            raise sqlite3.OperationalError("PostgreSQL opportunity read failed") from exc
