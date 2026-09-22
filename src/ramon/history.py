from __future__ import annotations

import sqlite3
from pathlib import Path

from .core import Bar, Market


HISTORY_SCHEMA = """
CREATE TABLE IF NOT EXISTS history_bars (
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    time INTEGER NOT NULL,
    open REAL NOT NULL,
    high REAL NOT NULL,
    low REAL NOT NULL,
    close REAL NOT NULL,
    spread_points INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (symbol, timeframe, time)
)
"""


def ensure_history_db(db: str | Path) -> Path:
    """Create Ramon's broker-history store when needed."""
    path = Path(db).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.execute(HISTORY_SCHEMA)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_history_symbol_tf_time "
            "ON history_bars(symbol,timeframe,time)"
        )
    return path


def persist_market(db: str | Path, market: Market) -> int:
    """Upsert completed bars from a live request; never store an incomplete candle."""
    path = ensure_history_db(db)
    spread_points = round((market.ask - market.bid) / market.point)
    newest = market.bars[-1].time
    rows = [
        (
            market.symbol,
            market.timeframe,
            bar.time,
            bar.open,
            bar.high,
            bar.low,
            bar.close,
            spread_points if bar.time == newest else 0,
        )
        for bar in market.bars
    ]
    with sqlite3.connect(path, timeout=10) as conn:
        conn.executemany(
            """
            INSERT INTO history_bars
                (symbol,timeframe,time,open,high,low,close,spread_points)
            VALUES (?,?,?,?,?,?,?,?)
            ON CONFLICT(symbol,timeframe,time) DO UPDATE SET
                open=excluded.open,
                high=excluded.high,
                low=excluded.low,
                close=excluded.close,
                spread_points=CASE
                    WHEN excluded.spread_points>0 THEN excluded.spread_points
                    ELSE history_bars.spread_points
                END
            """,
            rows,
        )
    return len(rows)


def history_count(db: str | Path, symbol: str = "XAUUSD_l") -> int:
    path = Path(db)
    if not path.is_file():
        return 0
    with sqlite3.connect(path) as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM history_bars WHERE symbol=? AND timeframe='M15'",
            (symbol,),
        ).fetchone()
    return int(row[0] if row else 0)


def load_bars(db: str | Path, symbol: str = "XAUUSD_l") -> tuple[tuple[Bar, ...], tuple[int, ...]]:
    """Read Ramon's or the existing bridge's historical M15 schema."""
    if not Path(db).is_file():
        raise FileNotFoundError(db)
    with sqlite3.connect(db) as conn:
        rows = conn.execute(
            """SELECT time,open,high,low,close,COALESCE(spread_points,0)
               FROM history_bars WHERE symbol=? AND timeframe='M15'
               ORDER BY time""",
            (symbol,),
        ).fetchall()
    bars = tuple(Bar(int(t), float(o), float(h), float(l), float(c)) for t, o, h, l, c, _ in rows)
    spreads = tuple(int(value) for *_, value in rows)
    if not bars:
        raise ValueError(f"no {symbol}/M15 history in {db}")
    return bars, spreads
