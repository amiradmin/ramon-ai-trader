"""Verify exact joins, real cost accounting, and honest missing-data reporting."""
import json
import sqlite3

from ramon.decision_journey import build_report, print_report
from ramon.history import ensure_history_db


def seed(db):
    ensure_history_db(db)
    bar = 1_800_000_000
    metadata = {"decision_audit": {
        "base": {"decision": "BUY", "reason": "forecast_up", "forecast_median": 104.0},
        "final": {"decision": "BUY", "reason": "forecast_up"},
        "settings": {"horizon": 4},
    }}
    with sqlite3.connect(db) as con:
        con.execute("""INSERT INTO decision_samples
            (captured,symbol,signal_bar_time,mid,spread,atr,direction,base_decision,
             regime_features,entry_features,meta_base_features,sample_key,quote_time,
             final_decision,model_metadata,chronos_model)
            VALUES (?, 'XAUUSD_l', ?, 100.2, 0.4, 1, 'BUY', 'BUY', '{}', '{}', '{}',
                    'aaaaaaaaaaaaaaaa', ?, 'BUY', ?, 'test/chronos')""",
                    (bar + 905, bar, bar + 905, json.dumps(metadata)))
        con.execute("""INSERT INTO decision_samples
            (captured,symbol,signal_bar_time,mid,spread,atr,direction,base_decision,
             regime_features,entry_features,meta_base_features,sample_key,quote_time,final_decision)
            VALUES (?, 'XAUUSD_l', ?, 100.2, 0.4, 1, 'NONE', 'WAIT', '{}', '{}', '{}',
                    'cccccccccccccccc', ?, 'WAIT')""", (bar + 910, bar, bar + 910))
        con.execute("""INSERT INTO trade_outcomes
            (trade_key,sample_key,symbol,direction,opened,closed,net_units,initial_risk_units,
             net_r,exit_reason,received,profit_units,commission_units,swap_units,fee_units,
             opened_utc_offset_seconds,closed_utc_offset_seconds)
            VALUES ('t1','aaaaaaaaaaaaaaaa','XAUUSD_l','BUY',?,?,7.5,6,1.25,
                    'DEAL_REASON_TP',?,10,-2,-0.4,-0.1,10800,10800)""",
                    (bar + 907, bar + 7200, bar + 7201))
        con.execute("""INSERT INTO trade_outcomes
            (trade_key,sample_key,symbol,direction,opened,closed,net_units,initial_risk_units,
             net_r,exit_reason,received)
            VALUES ('t2','bbbbbbbbbbbbbbbb','XAUUSD_l','SELL',?,?, -6,6,-1,
                    'DEAL_REASON_SL',?)""", (bar + 1000, bar + 7300, bar + 7301))
        for i in range(1, 5):
            con.execute("""INSERT INTO history_bars
                (symbol,timeframe,time,open,high,low,close,spread_points)
                VALUES ('XAUUSD_l','M15',?,100,105,98,?,40)""",
                (bar + i * 900, 100.0 + i))
    return bar


def test_exact_journey_forecast_and_real_costs_without_invented_fills(tmp_path, capsys):
    db = tmp_path / "history.sqlite3"
    seed(db)
    before = db.read_bytes()
    report = build_report(db, limit=0)
    assert db.read_bytes() == before
    assert report["coverage"]["saved_decisions"] == 2
    assert report["coverage"]["decisions_by_final_side"]["WAIT"] == 1
    assert report["coverage"]["closed_trades"] == 2
    assert report["coverage"]["joined_by_sample_key"] == 1
    assert report["coverage"]["forecast_error_available"] == 1
    assert report["coverage"]["complete_broker_fees"] == 1
    joined = next(x for x in report["trades"] if x["trade_key"] == "t1")
    assert joined["forecast_median"] == joined["realized_m15_bid_close"] == 104.0
    assert joined["forecast_error_bid"] == 0
    assert joined["quote_bid"] == 100.0
    assert joined["quote_ask"] == 100.4
    assert joined["quote_to_open_seconds"] == 2
    assert joined["broker_fees_units"] == -2.5
    assert joined["profit_units"] == 10
    assert joined["execution_fill_price"] is joined["slippage_price"] is None
    assert joined["opened"].endswith("UTC")
    legacy = next(x for x in report["trades"] if x["trade_key"] == "t2")
    assert legacy["decision_joined"] is False
    assert legacy["broker_fees_units"] is None
    assert legacy["opened"].endswith("BROKER[UTC offset unknown]")
    print_report(report)
    output = capsys.readouterr().out
    assert "forecast_up" in output
    assert "UNKNOWN" in output


def test_missing_horizon_bar_does_not_create_forecast_error(tmp_path):
    db = tmp_path / "history.sqlite3"
    bar = seed(db)
    with sqlite3.connect(db) as con:
        con.execute("DELETE FROM history_bars WHERE time=?", (bar + 1800,))
    assert build_report(db)["coverage"]["forecast_error_available"] == 0
    assert build_report(db, limit=1)["coverage"]["closed_trades"] == 2
    assert len(build_report(db, limit=1)["trades"]) == 1


def test_legacy_schema_is_read_without_migration(tmp_path):
    db = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(db) as con:
        con.execute("""CREATE TABLE decision_samples
            (symbol TEXT, captured INTEGER, base_decision TEXT)""")
        con.execute("""CREATE TABLE trade_outcomes
            (trade_key TEXT, sample_key TEXT, symbol TEXT, direction TEXT, opened INTEGER,
             closed INTEGER, net_units REAL, net_r REAL, exit_reason TEXT)""")
        con.execute("INSERT INTO decision_samples VALUES ('XAUUSD_l',1,'BUY')")
        con.execute("INSERT INTO trade_outcomes VALUES ('t','s','XAUUSD_l','BUY',1,2,1,0.5,'TP')")
    report = build_report(db)
    assert report["coverage"]["joined_by_sample_key"] == 0
    assert report["trades"][0]["broker_fees_units"] is None
    with sqlite3.connect(db) as con:
        assert len(con.execute("PRAGMA table_info(decision_samples)").fetchall()) == 3
