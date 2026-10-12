import sqlite3

from ramon.entry_timing_v2_walkforward import evaluate


def make_db(tmp_path, length=700):
    db = tmp_path / "history.sqlite3"
    start = 1_800_000_000
    with sqlite3.connect(db) as con:
        con.execute("""CREATE TABLE mtf_utc_bars(
          symbol TEXT,timeframe TEXT,time INTEGER,close REAL,spread_points INTEGER)""")
        for i in range(length):
            v=4200+i*0.04+((-1)**i)*0.14
            con.execute("INSERT INTO mtf_utc_bars VALUES(?,?,?,?,?)",
                        ("XAUUSD_l","M1",start+i*60,v,42))
        for i in range(length//5):
            v=4200+i*0.2
            con.execute("INSERT INTO mtf_utc_bars VALUES(?,?,?,?,?)",
                        ("XAUUSD_l","M5",start+i*300,v,42))
    return db


def test_multiple_forward_folds_are_read_only(tmp_path):
    db=make_db(tmp_path)
    r=evaluate(db,folds=4,train_fraction=0.5)
    assert r["promotion_ready"] is False
    assert r["status"] == "RESEARCH_ONLY"
    assert len(r["folds"]) == 4
    assert all(f["train_until_utc"] <= f["test_from_utc"] for f in r["folds"])
    assert all(f["test_from_utc"] <= f["test_until_utc"] for f in r["folds"])
    assert all(f["strategies"]["momentum"]["test"]["trades"] > 0 for f in r["folds"])
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT count(*) FROM mtf_utc_bars").fetchone()[0] == 840


def test_short_history_fails_closed(tmp_path):
    db=make_db(tmp_path,length=100)
    r=evaluate(db,folds=4)
    assert r["status"] == "INSUFFICIENT_HISTORY"
    assert r["folds"] == []
    assert r["promotion_ready"] is False
