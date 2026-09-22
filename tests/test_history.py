from __future__ import annotations

from pathlib import Path

from ramon.core import Bar, Market
from ramon.history import history_count, load_bars, persist_market


def test_persist_market_deduplicates_completed_bars(tmp_path: Path) -> None:
    bars = tuple(
        Bar(1_800_000_000 + i * 900, 100.0, 101.0, 99.0, 100.0 + i * 0.01)
        for i in range(128)
    )
    market = Market("XAUUSD_l", "M15", 101.0, 101.42, 0.01, bars)
    db = tmp_path / "history.sqlite3"

    assert persist_market(db, market) == 128
    assert persist_market(db, market) == 128
    assert history_count(db) == 128

    loaded, spreads = load_bars(db)
    assert loaded[-1].time == bars[-1].time
    assert spreads[-1] == 42
