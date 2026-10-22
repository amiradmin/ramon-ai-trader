"""Read-only PostgreSQL market-history adapter for migration verification.

No live service uses this module unless explicitly invoked. Keeps trading
execution and SQLite writers untouched.
"""
from __future__ import annotations

from .core import Bar


def load_bars_postgres(dsn: str, symbol: str = "XAUUSD_l") -> tuple[tuple[Bar, ...], tuple[int, ...]]:
    """Return typed PostgreSQL M15 bars using the same contract as history.load_bars."""
    if not dsn:
        raise ValueError("PostgreSQL DSN is required")
    try:
        import psycopg
    except ImportError as exc:
        raise RuntimeError("install optional postgres dependencies: uv sync --extra postgres") from exc
    with psycopg.connect(dsn, connect_timeout=5) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT time,open,high,low,close,COALESCE(spread_points,0)
                   FROM ramon_migration.history_bars
                   WHERE symbol=%s AND timeframe='M15'
                   ORDER BY time""",
                (symbol,),
            )
            rows = cur.fetchall()
    bars = tuple(Bar(int(t), float(o), float(h), float(l), float(c)) for t, o, h, l, c, _ in rows)
    spreads = tuple(int(row[5]) for row in rows)
    if not bars:
        raise ValueError(f"no {symbol}/M15 history in PostgreSQL")
    return bars, spreads
