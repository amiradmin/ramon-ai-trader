import sqlite3
from ramon.reversal_regime_audit import _regime, evaluate


def test_regime_uses_only_past_closes():
    at=1_800_000_000
    closes={at-i*60:4200+i*.1 for i in range(1,17)}
    assert _regime(closes,at)=="TREND_PROXY"
    closes[at+60]=9000
    assert _regime(closes,at)=="TREND_PROXY"


def test_utc_hour_regime_audit_remains_observe_only(tmp_path):
    db=tmp_path/"bars.sqlite3"
    start=1_800_000_000
    with sqlite3.connect(db) as c:
        c.execute("CREATE TABLE mtf_utc_bars(symbol TEXT,timeframe TEXT,time INTEGER,close REAL,spread_points INTEGER)")
        for i in range(650):
            price=4200+i*.03+(-1)**i*.17
            c.execute("INSERT INTO mtf_utc_bars VALUES(?,?,?,?,?)",("XAUUSD_l","M1",start+i*60,price,42))
        for i in range(130):
            price=4200+i*.15
            c.execute("INSERT INTO mtf_utc_bars VALUES(?,?,?,?,?)",("XAUUSD_l","M5",start+i*300,price,42))
    r=evaluate(db)
    assert r["promotion_ready"] is False
    assert r["status"]=="INSUFFICIENT_INDEPENDENT_DAYS"
    assert len(r["folds"])==4
    assert r["independent_utc_days"]<5
    assert r["overall"]["trades"]>0
    with sqlite3.connect(db) as c:
        assert c.execute("SELECT COUNT(*) FROM mtf_utc_bars").fetchone()[0]==780
