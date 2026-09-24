from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from ramon.core import Bar, Market
from ramon.history import ensure_history_db, persist_decision_sample, persist_market, persist_trade_outcome
from ramon.target_outcomes import backfill_target_outcomes


def _seed(db: Path) -> None:
    bars = tuple(
        Bar(1_800_000_000 + i * 900, 100, 101 + i * 0.2, 99, 100.5)
        for i in range(128)
    )
    market = Market("XAUUSD_l", "M15", 100.0, 100.4, 0.01, bars)
    persist_market(db, market)
    sample_key = "a" * 16
    persist_decision_sample(
        db,
        captured=1_900_000_000,
        market=market,
        signal_bar_time=bars[-1].time,
        atr=2.0,
        direction="BUY",
        base_decision="BUY",
        regime_features={"ret_1_atr": 0.1},
        entry_features={"edge_ratio": 1.0},
        meta_base_features={"base_trade": 1.0},
        news_features={"event_risk": 0.0},
        sample_key=sample_key,
        chronos_model="test/fake",
        quote_time=bars[-1].time + 900,
        stop_distance=3.0,
        target_distance=6.0,
        final_decision="BUY",
        target_structure={
            "ready": 1,
            "method": "counter_impulse_retrace",
            "direction": "BUY",
            "impulse_start": 106.0,
            "impulse_end": 100.0,
            "impulse_range": 6.0,
            "impulse_atr": 3.0,
            "tp1": 103.0,
            "tp2": 106.0,
            "tp3": 109.0,
            "legacy_target": 106.0,
        },
    )
    with sqlite3.connect(ensure_history_db(db)) as con:
        con.execute(
            "INSERT OR REPLACE INTO history_bars VALUES (?,?,?,?,?,?,?,?)",
            ("XAUUSD_l", "M15", bars[-1].time + 900, 100, 104, 99, 103.5, 42),
        )
        con.execute(
            "INSERT OR REPLACE INTO history_bars VALUES (?,?,?,?,?,?,?,?)",
            ("XAUUSD_l", "M15", bars[-1].time + 1800, 103.5, 107, 103, 106.5, 42),
        )
    persist_trade_outcome(db, {
        "trade_key": "server:1:2", "sample_key": sample_key, "symbol": "XAUUSD_l",
        "direction": "BUY", "opened": bars[-1].time + 905, "closed": bars[-1].time + 1800,
        "net_units": 6.0, "initial_risk_units": 6.0, "exit_reason": "DEAL_REASON_TP",
    }, 1_900_001_000)


def test_backfill_target_outcomes_labels_tp1_and_tp2(tmp_path: Path) -> None:
    db = tmp_path / "ramon.sqlite3"
    _seed(db)
    result = backfill_target_outcomes(db)
    assert result["inserted"] == 1
    with sqlite3.connect(db) as con:
        row = con.execute(
            "SELECT tp1_hit,tp2_hit,tp3_hit,continuation_tp2,continuation_tp3 FROM target_outcomes"
        ).fetchone()
    assert row == (1, 1, 0, 1, 0)


def test_backfill_is_idempotent(tmp_path: Path) -> None:
    db = tmp_path / "ramon.sqlite3"
    _seed(db)
    assert backfill_target_outcomes(db)["inserted"] == 1
    assert backfill_target_outcomes(db)["inserted"] == 0
