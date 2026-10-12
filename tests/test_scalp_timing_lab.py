import sqlite3
from ramon.scalp_timing_lab import evaluate


def test_shadow_audit_uses_closed_bars_and_does_not_change_database(tmp_path):
    db=tmp_path/"audit.sqlite3"
    with sqlite3.connect(db) as con:
        con.execute("""CREATE TABLE mtf_utc_bars(
            symbol TEXT,timeframe TEXT,time INTEGER,open REAL,high REAL,
            low REAL,close REAL)""")
        start=1_800_000_000
        for i in range(46):
            t=start+i*60
            price=4000+i*.1
            con.execute("INSERT INTO mtf_utc_bars VALUES(?,?,?,?,?,?,?)",
                        ("XAUUSD_l","M1",t,price,price+.2,price-.2,price))
        for i in range(10):
            t=start+i*300
            price=4000+i*.5
            con.execute("INSERT INTO mtf_utc_bars VALUES(?,?,?,?,?,?,?)",
                        ("XAUUSD_l","M5",t,price,price+.5,price-.5,price))
    out=evaluate(db,horizon_minutes=5,spread_price=0.42)
    assert out["eligible_decision_times"]>0
    assert out["results"]["BUY"]["signals"]>0
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT count(*) FROM mtf_utc_bars").fetchone()[0]==56


def test_insufficient_data_returns_zero_signals(tmp_path):
    db=tmp_path/"empty.sqlite3"
    with sqlite3.connect(db) as con:
        con.execute("CREATE TABLE mtf_utc_bars(symbol TEXT,timeframe TEXT,time INTEGER,close REAL)")
    out=evaluate(db)
    assert out["results"]["BUY"]["signals"]==0
