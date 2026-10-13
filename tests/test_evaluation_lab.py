import sqlite3
from ramon.evaluation_lab import evaluate


def test_read_only_auto_filter_and_wait_caveats(tmp_path):
    db = tmp_path / "history.db"
    with sqlite3.connect(db) as con:
        con.executescript("""
            CREATE TABLE decision_samples(symbol TEXT,sample_key TEXT,final_decision TEXT);
            CREATE TABLE trade_outcomes(symbol TEXT,sample_key TEXT,entry_source TEXT,
              net_units REAL,net_r REAL,closed REAL,manual_intervention TEXT);
        """)
        con.executemany("INSERT INTO decision_samples VALUES (?,?,?)", [
            ("XAUUSD_l","k1","BUY"), ("XAUUSD_l","k2","SELL"),
            ("XAUUSD_l","k3","WAIT")])
        con.executemany("INSERT INTO trade_outcomes VALUES (?,?,?,?,?,?,?)", [
            ("XAUUSD_l","k1","AUTO_RAMON",5.0,1.0,1000,None),
            ("XAUUSD_l","k2","RANGE_AUTO",-3.0,-0.6,1001,None),
            ("XAUUSD_l","k2","AUTO_RAMON",9,1.0,1002,"SL_MOVED_BY_HUMAN"),
            ("XAUUSD_l","k3","HUMAN_ASSISTED",100,10,1003,None),
            ("XAUUSD_l","missing","AUTO_RAMON",4,0.5,1004,None)])
    report = evaluate(db)
    assert report["available"]
    assert report["data_audit"]["wait_decisions"] == 1
    assert report["auto_trades"]["candidate_count"] == 2
    assert report["auto_trades"]["net_units"] == 2.0
    assert report["auto_trades"]["profit_factor"] == 5/3
    assert report["auto_trades"]["ready_for_training"] is False
    assert report["data_audit"]["unmatched_auto_samples"] == 1
    assert report["wait_replay"]["status"] == "NOT_EVALUATED"
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT count(*) FROM trade_outcomes").fetchone()[0] == 5


def test_missing_manual_intervention_column_is_fail_closed(tmp_path):
    db = tmp_path / "history.db"
    with sqlite3.connect(db) as con:
        con.execute("CREATE TABLE decision_samples(symbol TEXT,sample_key TEXT,final_decision TEXT)")
        con.execute("CREATE TABLE trade_outcomes(symbol TEXT,sample_key TEXT,entry_source TEXT,net_units REAL,net_r REAL,closed REAL)")
        con.execute("INSERT INTO decision_samples VALUES ('XAUUSD_l','k1','BUY')")
        con.execute("INSERT INTO trade_outcomes VALUES ('XAUUSD_l','k1','AUTO_RAMON',3,1,1000)")
    report = evaluate(db)
    assert report["auto_trades"]["candidate_count"] == 0
    assert report["data_audit"]["exclusion_reasons"]["INTERVENTION_COLUMN_MISSING"] == 1
