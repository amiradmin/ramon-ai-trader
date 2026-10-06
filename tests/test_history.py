from __future__ import annotations

from pathlib import Path
import sqlite3

from ramon.core import Bar, Market
from ramon.history import (
    decision_sample_count,
    history_count,
    ensure_history_db,
    load_bars,
    persist_decision_sample,
    persist_market,
    persist_trade_outcome,
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



def test_trade_outcomes_allow_multiple_trades_for_one_sample(tmp_path: Path) -> None:
    db = tmp_path / "history.sqlite3"
    ensure_history_db(db)
    base = {
        "sample_key": "a" * 16,
        "symbol": "XAUUSD_l",
        "direction": "BUY",
        "opened": 1_900_000_000,
        "closed": 1_900_000_060,
        "net_units": 1.0,
        "initial_risk_units": 2.0,
        "exit_reason": "DEAL_REASON_TP",
    }
    persist_trade_outcome(db, {**base, "trade_key": "trade:1"}, 1_900_000_100)
    persist_trade_outcome(db, {**base, "trade_key": "trade:2", "closed": 1_900_000_070}, 1_900_000_110)
    # Re-delivery of the same trade_key remains idempotent.
    persist_trade_outcome(db, {**base, "trade_key": "trade:1"}, 1_900_000_120)

    with sqlite3.connect(db) as con:
        rows = con.execute(
            "SELECT trade_key,sample_key FROM trade_outcomes ORDER BY trade_key"
        ).fetchall()
    assert rows == [("trade:1", "a" * 16), ("trade:2", "a" * 16)]


def test_legacy_trade_outcome_schema_migrates_shared_sample_key(tmp_path: Path) -> None:
    db = tmp_path / "history.sqlite3"
    with sqlite3.connect(db) as con:
        con.execute("""CREATE TABLE trade_outcomes (
            trade_key TEXT PRIMARY KEY, sample_key TEXT NOT NULL UNIQUE, symbol TEXT NOT NULL,
            direction TEXT NOT NULL, opened INTEGER NOT NULL, closed INTEGER NOT NULL,
            net_units REAL NOT NULL, initial_risk_units REAL NOT NULL,
            net_r REAL NOT NULL, exit_reason TEXT NOT NULL, received INTEGER NOT NULL
        )""")
        con.execute(
            "INSERT INTO trade_outcomes VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            ("legacy:1", "b" * 16, "XAUUSD_l", "SELL", 10, 20, 1.0, 2.0, 0.5, "DEAL_REASON_TP", 30),
        )

    ensure_history_db(db)
    persist_trade_outcome(db, {
        "trade_key": "legacy:2", "sample_key": "b" * 16, "symbol": "XAUUSD_l",
        "direction": "SELL", "opened": 11, "closed": 21,
        "net_units": -1.0, "initial_risk_units": 2.0, "exit_reason": "DEAL_REASON_SL",
    }, 31)

    with sqlite3.connect(db) as con:
        assert con.execute(
            "SELECT COUNT(*) FROM trade_outcomes WHERE sample_key=?", ("b" * 16,)
        ).fetchone()[0] == 2



def test_legacy_trade_migration_recovers_from_stale_staging_table(tmp_path: Path) -> None:
    db = tmp_path / "history.sqlite3"
    with sqlite3.connect(db) as con:
        con.execute("""CREATE TABLE trade_outcomes (
            trade_key TEXT PRIMARY KEY, sample_key TEXT NOT NULL UNIQUE, symbol TEXT NOT NULL,
            direction TEXT NOT NULL, opened INTEGER NOT NULL, closed INTEGER NOT NULL,
            net_units REAL NOT NULL, initial_risk_units REAL NOT NULL,
            net_r REAL NOT NULL, exit_reason TEXT NOT NULL, received INTEGER NOT NULL
        )""")
        con.execute("""CREATE TABLE trade_outcomes_v2 (
            trade_key TEXT PRIMARY KEY, sample_key TEXT NOT NULL, symbol TEXT NOT NULL,
            direction TEXT NOT NULL, opened INTEGER NOT NULL, closed INTEGER NOT NULL,
            net_units REAL NOT NULL, initial_risk_units REAL NOT NULL,
            net_r REAL NOT NULL, exit_reason TEXT NOT NULL, received INTEGER NOT NULL
        )""")
        con.execute(
            "INSERT INTO trade_outcomes VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            ("legacy:stale", "c" * 16, "XAUUSD_l", "BUY", 10, 20, 1.0, 2.0, 0.5, "DEAL_REASON_TP", 30),
        )

    ensure_history_db(db)

    with sqlite3.connect(db) as con:
        table_sql = con.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='trade_outcomes'"
        ).fetchone()[0]
        assert "sample_key TEXT NOT NULL UNIQUE" not in table_sql
        assert con.execute(
            "SELECT trade_key,sample_key FROM trade_outcomes"
        ).fetchall() == [("legacy:stale", "c" * 16)]
        assert con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='trade_outcomes_v2'"
        ).fetchone() is None



def test_legacy_trade_migration_preserves_unknown_future_columns(tmp_path: Path) -> None:
    db = tmp_path / "history.sqlite3"
    with sqlite3.connect(db) as con:
        con.execute("""CREATE TABLE trade_outcomes (
            trade_key TEXT PRIMARY KEY, sample_key TEXT NOT NULL UNIQUE, symbol TEXT NOT NULL,
            direction TEXT NOT NULL, opened INTEGER NOT NULL, closed INTEGER NOT NULL,
            net_units REAL NOT NULL, initial_risk_units REAL NOT NULL,
            net_r REAL NOT NULL, exit_reason TEXT NOT NULL, received INTEGER NOT NULL,
            entry_strategy TEXT
        )""")
        con.execute(
            "INSERT INTO trade_outcomes VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            ("future:1", "d" * 16, "XAUUSD_l", "BUY", 10, 20, 1.0, 2.0, 0.5,
             "DEAL_REASON_TP", 30, "AUTO"),
        )

    ensure_history_db(db)

    with sqlite3.connect(db) as con:
        columns = {row[1] for row in con.execute("PRAGMA table_info(trade_outcomes)")}
        assert "entry_strategy" in columns
        assert con.execute(
            "SELECT entry_strategy FROM trade_outcomes WHERE trade_key='future:1'"
        ).fetchone()[0] == "AUTO"
