import json
import sqlite3

from ramon.core import Bar, Market
from ramon.history import persist_micro_market
from ramon.signal_outcomes import backfill_signal_outcomes, build_signal_outcome_report


def _schema(con):
    con.execute("""CREATE TABLE decision_samples (
        sample_key TEXT, symbol TEXT, quote_time INTEGER, captured INTEGER,
        mid REAL, atr REAL, direction TEXT, base_decision TEXT,
        final_decision TEXT, model_metadata TEXT
    )""")
    con.execute("""CREATE TABLE history_bars (
        symbol TEXT,timeframe TEXT,time INTEGER,open REAL,high REAL,low REAL,close REAL,
        spread_points INTEGER, PRIMARY KEY(symbol,timeframe,time)
    )""")


def test_false_block_is_measured_without_changing_decision(tmp_path):
    db = tmp_path / "x.sqlite3"
    with sqlite3.connect(db) as con:
        _schema(con)
        meta = {"decision_audit": {"base": {
            "reason": "insufficient_model_strength", "forecast_median": 101.0,
            "signal_strength": 0.15, "minimum_strength": 0.20,
            "buy_edge": 2.0, "sell_edge": 0.0, "minimum_edge": 0.8,
            "ai_trend_confirmed": 0, "ai_trend_direction": "BUY",
            "intrabar_confirmed": 0, "intrabar_direction": "BUY",
        }, "final": {}}}
        con.execute("INSERT INTO decision_samples VALUES (?,?,?,?,?,?,?,?,?,?)",
                    ("s1","XAUUSD_l",1000,1000,100.0,10.0,"BUY","WAIT","WAIT",
                     json.dumps(meta)))
        con.execute("INSERT INTO history_bars VALUES (?,?,?,?,?,?,?,?)",
                    ("XAUUSD_l","M1",1240,102,103.5,101.5,103.0,0))
    assert backfill_signal_outcomes(db, horizons=(300,), now=2000) == 1
    report = build_signal_outcome_report(db, horizon_seconds=300)
    assert report["false_blocks"] == 1
    assert report["direction"]["accuracy"] == 1.0
    assert report["false_block_components"] == {
        "strength_failed": 1, "intrabar_unconfirmed": 1, "ai_trend_unconfirmed": 1
    }


def test_persist_micro_market_excludes_open_m1_bar(tmp_path):
    db = tmp_path / "x.sqlite3"
    market = Market(
        symbol="XAUUSD_l", timeframe="M15", bid=100.0, ask=100.4, point=0.01,
        bars=tuple(Bar(100 + i * 900, 100, 101, 99, 100) for i in range(128)),
        micro_bars=(
            Bar(200000,100,101,99,100),
            Bar(200060,100,101,99,100),
            Bar(200120,100,101,99,100),
            Bar(200180,100,101,99,100),
        ),
    )
    assert persist_micro_market(db, market, 200210) == 3
    with sqlite3.connect(db) as con:
        times = [row[0] for row in con.execute(
            "SELECT time FROM history_bars WHERE timeframe='M1' ORDER BY time"
        )]
    assert times == [200000, 200060, 200120]
