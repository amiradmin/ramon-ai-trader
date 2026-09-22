from __future__ import annotations

import sqlite3
from pathlib import Path

from .core import Bar


def load_bars(db: str | Path, symbol: str = "XAUUSD_l") -> tuple[tuple[Bar, ...], tuple[int, ...]]:
    """Read the existing bridge's historical bar schema without modifying it."""
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
