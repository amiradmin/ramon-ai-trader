import sqlite3

from ramon.entry_timing_v2_lab import evaluate, STRATEGIES


def test_chronological_read_only_comparison(tmp_path):
    db = tmp_path / "history.sqlite3"
    start = 1_800_000_000  # divisible by 300
    with sqlite3.connect(db) as con:
        con.execute("CREATE TABLE mtf_utc_bars(symbol TEXT,timeframe TEXT,time INTEGER,close REAL)")
        for i in range(240):
            t = start + i * 60
            value = 4200 + i * 0.07
            con.execute("INSERT INTO mtf_utc_bars VALUES(?,?,?,?)",("XAUUSD_l","M1",t,value))
        for i in range(48):
            t = start + i * 300
            value = 4200 + i * 0.35
            con.execute("INSERT INTO mtf_utc_bars VALUES(?,?,?,?)",("XAUUSD_l","M5",t,value))
        before = con.execute("SELECT count(*) FROM mtf_utc_bars").fetchone()[0]
    out = evaluate(db, spread_price=0.42)
    assert out["mode"] == "OFFLINE_OBSERVE_ONLY"
    assert set(out["strategies"]) == set(STRATEGIES)
    assert out["train_selected"] in STRATEGIES
    assert out["promotion_ready"] is False
    assert out["strategies"]["momentum"]["train"]["trades"] > 0
    assert out["strategies"]["momentum"]["test"]["trades"] > 0
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT count(*) FROM mtf_utc_bars").fetchone()[0] == before


def test_tiny_dataset_fails_closed(tmp_path):
    db = tmp_path / "tiny.sqlite3"
    with sqlite3.connect(db) as con:
        con.execute("CREATE TABLE mtf_utc_bars(symbol TEXT,timeframe TEXT,time INTEGER,close REAL)")
    result = evaluate(db)
    assert result["train_selected"] is None
    assert result["promotion_ready"] is False
    assert result["selected_holdout"] is None
