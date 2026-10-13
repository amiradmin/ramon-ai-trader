"""Opt-in PostgreSQL source for M15 historical bars.

Only the M15 history path is supported. Other Ramon tables still require
SQLite. Keep configuration OFF for production until latency/parity validation.
"""
from __future__ import annotations

from .core import Bar


def load_m15_postgres(dsn: str, symbol: str) -> tuple[tuple[Bar, ...], tuple[int, ...]]:
    if not dsn:
        raise ValueError("RAMON_PG_HISTORY_DSN is required")
    import psycopg
    with psycopg.connect(dsn, connect_timeout=2, options="-c statement_timeout=3000") as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT time,open,high,low,close,COALESCE(spread_points,0)
                   FROM ramon_migration.history_bars
                   WHERE symbol=%s AND timeframe='M15'
                   ORDER BY time""",
                (symbol,),
            )
            rows = cur.fetchall()
    if not rows:
        raise ValueError(f"no {symbol}/M15 history in PostgreSQL")
    bars = tuple(Bar(int(t), float(o), float(h), float(l), float(c))
                 for t, o, h, l, c, _ in rows)
    return bars, tuple(int(row[5]) for row in rows)
