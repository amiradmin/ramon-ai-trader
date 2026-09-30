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



def test_legacy_capture_constraint_migration_preserves_rows_ids_and_indexes(tmp_path):
    import sqlite3
    from ramon.history import DECISION_SAMPLE_SCHEMA, ensure_history_db, persist_decision_sample
    db = tmp_path / 'old.sqlite3'
    legacy_schema = DECISION_SAMPLE_SCHEMA.replace('meta_base_features TEXT NOT NULL',
        'meta_base_features TEXT NOT NULL, UNIQUE(captured, symbol)')
    with sqlite3.connect(db) as conn:
        conn.execute(legacy_schema)
        conn.execute('CREATE INDEX custom_sample_direction ON decision_samples(direction)')
        conn.execute("INSERT INTO decision_samples VALUES(7,1900000000,'XAUUSD_l',1800000000,100,.4,2,'BUY','WAIT','{}','{}','{}')")
        conn.execute("UPDATE sqlite_sequence SET seq=20 WHERE name='decision_samples'")
    ensure_history_db(db)
    ensure_history_db(db)  # migration is idempotent
    market = Market('XAUUSD_l', 'M15', 100., 100.4, .01,
        (Bar(1800000000,100,101,99,100),))
    args = dict(captured=1900000000,market=market,signal_bar_time=1800000000,atr=2,
        direction='BUY',base_decision='WAIT',regime_features={},entry_features={},meta_base_features={})
    assert persist_decision_sample(db, sample_key='a'*16, **args)
    assert persist_decision_sample(db, sample_key='b'*16, **args)
    assert not persist_decision_sample(db, sample_key='a'*16, **args)
    assert not persist_decision_sample(db, **args)  # legacy capture dedup preserved
    with sqlite3.connect(db) as conn:
        rows = conn.execute('SELECT id,sample_key FROM decision_samples ORDER BY id').fetchall()
        assert rows == [(7,None),(21,'a'*16),(22,'b'*16)]
        assert conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='custom_sample_direction'").fetchone()[0] == 1
        assert conn.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'


def test_failed_sample_migration_rolls_back_original_rows(tmp_path):
    import sqlite3
    import pytest
    from ramon.history import DECISION_SAMPLE_SCHEMA, ensure_history_db
    db=tmp_path/'migration_failure.sqlite3'
    with sqlite3.connect(db) as conn:
        conn.execute(DECISION_SAMPLE_SCHEMA.replace('meta_base_features TEXT NOT NULL',
            'meta_base_features TEXT NOT NULL, UNIQUE(captured, symbol)'))
        conn.execute("INSERT INTO decision_samples VALUES(1,1900000000,'XAUUSD_l',1800000000,100,.4,2,'BUY','WAIT','{}','{}','{}')")
        # Deliberately occupied staging name forces an error after migration starts.
        conn.execute('CREATE TABLE decision_samples_v063 (id INTEGER)')
    with pytest.raises(sqlite3.OperationalError):
        ensure_history_db(db)
    with sqlite3.connect(db) as conn:
        assert conn.execute('SELECT COUNT(*),SUM(id) FROM decision_samples').fetchone()==(1,1)
        assert 'UNIQUE(captured, symbol)' in conn.execute("SELECT sql FROM sqlite_master WHERE name='decision_samples'").fetchone()[0]
        assert 'sample_key' not in {r[1] for r in conn.execute('PRAGMA table_info(decision_samples)')}
