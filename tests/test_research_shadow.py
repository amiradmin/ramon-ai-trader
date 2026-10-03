import sqlite3

import pytest

from ramon.core import Bar
from ramon.history import ensure_history_db, load_shadow_votes, persist_shadow_vote
from ramon.research_registry import candidate, registry
from ramon.smc_features import smc_technical_features


def sample_bars(n=80):
    bars = []
    for i in range(n):
        base = 2000.0 + i * 0.4
        open_ = base
        close = base + (0.3 if i % 3 else -0.1)
        bars.append(Bar(
            1_800_000_000 + i * 900,
            open_,
            max(open_, close) + 0.5,
            min(open_, close) - 0.4,
            close,
        ))
    return tuple(bars)


def test_research_registry_is_shadow_safe():
    rows = registry()
    assert rows
    assert candidate("hf_smc_v2").status == "FEATURE_SOURCE"
    assert candidate("hf_ppo_gold").status == "SHADOW_ONLY"
    assert all(row["status"] != "LIVE" for row in rows)


def test_smc_features_are_finite_and_past_only():
    bars = sample_bars()
    first = smc_technical_features(bars)
    changed_future = bars + (Bar(bars[-1].time + 900, 9999, 10000, 9998, 9999),)
    second = smc_technical_features(changed_future[:-1])
    assert first == second
    required = {
        "SMA_20", "SMA_50", "EMA_12", "EMA_26", "RSI",
        "MACD", "MACD_signal", "MACD_hist",
        "BB_upper", "BB_middle", "BB_lower",
        "FVG_Size", "FVG_Type", "OB_Type", "Recovery_Type",
        "Close_lag1", "Close_lag2", "Close_lag3",
    }
    assert required <= set(first)
    assert 0 <= first["RSI"] <= 100


def test_smc_features_require_history():
    with pytest.raises(ValueError, match="60"):
        smc_technical_features(sample_bars(20))


def test_shadow_votes_are_idempotent_and_never_touch_decisions(tmp_path):
    db = tmp_path / "history.sqlite3"
    ensure_history_db(db)
    key = "smc-shadow:1800000000"
    kwargs = dict(
        db=db,
        vote_key=key,
        captured=1_800_000_010,
        symbol="XAUUSD_l",
        signal_bar_time=1_800_000_000,
        advisor="smc_local_research",
        decision="BUY",
        confidence=0.61,
        regime="TREND_UP",
        metadata={"feature_schema": 1},
        source_version="research-v1",
    )
    assert persist_shadow_vote(**kwargs)
    assert not persist_shadow_vote(**kwargs)

    votes = load_shadow_votes(db)
    assert len(votes) == 1
    assert votes[0]["decision"] == "BUY"
    assert votes[0]["metadata"]["feature_schema"] == 1

    with sqlite3.connect(db) as conn:
        # Shadow infrastructure must not create or mutate live decision rows.
        assert conn.execute("SELECT COUNT(*) FROM decision_samples").fetchone()[0] == 0


def test_shadow_vote_validation(tmp_path):
    db = tmp_path / "history.sqlite3"
    with pytest.raises(ValueError, match="decision"):
        persist_shadow_vote(
            db,
            vote_key="advisor:12345678",
            captured=1,
            symbol="XAUUSD_l",
            signal_bar_time=1,
            advisor="x",
            decision="HOLD",
        )
