import sqlite3
from ramon.missed_entry_audit import audit


def test_time_scoped_audit_and_no_hindsight(tmp_path):
    path=tmp_path/"r.sqlite3"
    with sqlite3.connect(path) as db:
        db.executescript("""
          CREATE TABLE decision_samples(id INTEGER PRIMARY KEY,captured INTEGER,symbol TEXT,
            sample_key TEXT,final_decision TEXT,final_reason TEXT,model_metadata TEXT);
          CREATE TABLE trade_outcomes(symbol TEXT,opened INTEGER,entry_source TEXT);
          INSERT INTO decision_samples VALUES(1,100,'XAUUSD_l','a','WAIT','direction_confirmation_required',
            '{"decision_audit":{"final":{"market_state":"REGIME_TRANSITION","candidate_direction":"BUY","market_direction":"SELL"}}}');
          INSERT INTO decision_samples VALUES(2,200,'XAUUSD_l','b','BUY','forecast_up','{}');
          INSERT INTO decision_samples VALUES(3,300,'XAUUSD_l','c','WAIT','spread_or_atr','{}');
          INSERT INTO trade_outcomes VALUES('XAUUSD_l',210,'AUTO_RAMON');
        """)
    data=audit(path,since_utc=99,until_utc=250)
    assert data["available"]
    assert data["total_decision_samples"]==2
    assert data["decisions"]=={"BUY":1,"WAIT":1}
    assert data["direction_conflicts"]==1
    assert data["recorded_trade_outcomes_opened"]==1
    assert data["missed_profit_or_should_have_entered"]=="NOT_EVALUATED"


def test_invalid_window_fail_closed(tmp_path):
    assert audit(tmp_path/"no.sqlite3", since_utc=200,until_utc=100)["reason"]=="INVALID_TIME_WINDOW"
