"""Optional, best-effort M15 market-bar shadow mirror into isolated PostgreSQL.

SQLite remains authoritative. This module is not used unless explicitly
configured, and must never affect trading decisions.
"""
from __future__ import annotations

from .core import Market


def mirror_market_postgres(dsn: str, market: Market) -> int:
    """Upsert completed live market bars using the same spread policy as SQLite."""
    if not dsn:
        return 0
    if not market.bars:
        raise ValueError("cannot mirror an empty market snapshot")
    try:
        import psycopg
    except ImportError as exc:
        raise RuntimeError("install psycopg PostgreSQL optional dependency") from exc
    newest = market.bars[-1].time
    spread_points = round((market.ask - market.bid) / market.point)
    rows = [(market.symbol, market.timeframe, int(bar.time),
             float(bar.open), float(bar.high), float(bar.low), float(bar.close),
             spread_points if bar.time == newest else 0)
            for bar in market.bars]
    with psycopg.connect(dsn, connect_timeout=2) as conn:
        with conn.cursor() as cur:
            cur.executemany(
                """INSERT INTO ramon_migration.history_bars
                   (symbol,timeframe,time,open,high,low,close,spread_points)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT(symbol,timeframe,time) DO UPDATE SET
                     open=EXCLUDED.open,high=EXCLUDED.high,low=EXCLUDED.low,
                     close=EXCLUDED.close,
                     spread_points=CASE WHEN EXCLUDED.spread_points>0
                          THEN EXCLUDED.spread_points
                          ELSE ramon_migration.history_bars.spread_points END""",
                rows,
            )
    return len(rows)
