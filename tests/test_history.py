from __future__ import annotations

from pathlib import Path

from ramon.core import Bar, Market
from ramon.history import (
    decision_sample_count,
    history_count,
    load_bars,
    persist_decision_sample,
    persist_market,
)


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



def test_persist_decision_sample_for_role_training(tmp_path: Path) -> None:
    bars = tuple(
        Bar(1_800_000_000 + i * 900, 100.0, 101.0, 99.0, 100.0 + i * 0.01)
        for i in range(128)
    )
    market = Market("XAUUSD_l", "M15", 101.0, 101.42, 0.01, bars)
    db = tmp_path / "history.sqlite3"

    persist_decision_sample(
        db,
        captured=1_900_000_000,
        market=market,
        signal_bar_time=bars[-1].time,
        atr=2.0,
        direction="BUY",
        base_decision="WAIT",
        regime_features={"ret_1_atr": 0.1},
        entry_features={"edge_ratio": 0.8},
        meta_base_features={"base_trade": 0.0},
    )
    persist_decision_sample(
        db,
        captured=1_900_000_000,
        market=market,
        signal_bar_time=bars[-1].time,
        atr=2.0,
        direction="BUY",
        base_decision="WAIT",
        regime_features={"ret_1_atr": 0.1},
        entry_features={"edge_ratio": 0.8},
        meta_base_features={"base_trade": 0.0},
    )

    assert decision_sample_count(db) == 1
