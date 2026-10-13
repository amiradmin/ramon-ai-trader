import sqlite3
from ramon.early_exit_replay_validator import validate


def test_validator_never_invents_replay_pnl(tmp_path):
    path = tmp_path / "history.sqlite3"
    with sqlite3.connect(path) as db:
        db.executescript("""
            CREATE TABLE decision_samples(symbol TEXT,sample_key TEXT,final_decision TEXT,
                stop_distance REAL,target_distance REAL);
            CREATE TABLE trade_outcomes(symbol TEXT,sample_key TEXT,trade_key TEXT,
                entry_source TEXT,net_units REAL,net_r REAL,opened REAL,closed REAL,
                manual_intervention TEXT,exit_detail TEXT,direction TEXT,actual_fill_price REAL);
            CREATE TABLE history_bars(symbol TEXT,timeframe TEXT,time INTEGER,
                open REAL,high REAL,low REAL,close REAL,spread_points INTEGER);
            INSERT INTO decision_samples VALUES('XAUUSD_l','k1','BUY',8,16);
            INSERT INTO trade_outcomes VALUES('XAUUSD_l','k1','trade1','AUTO_RAMON',
                -4,-.5,600,660,NULL,'early_adverse_exit','BUY',4200);
        """)
    data = validate(path,lookahead_minutes=5)
    assert data["available"]
    assert data["candidate_trades"] == 1
    assert data["trades"][0]["planned_levels"]["sl"] == 4192
    assert data["trades"][0]["hypothetical_net_units"] is None
    assert "M1_COVERAGE_INCOMPLETE" in data["trades"][0]["blockers"]
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT count(*) FROM trade_outcomes").fetchone()[0] == 1
