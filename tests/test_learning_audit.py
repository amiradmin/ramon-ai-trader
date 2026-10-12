import sqlite3

from ramon.learning_audit import audit


def test_missing_database(tmp_path):
    result = audit(tmp_path / "missing.sqlite3")
    assert result["status"] == "DB_MISSING"
    assert not result["available"]


def test_missing_schema(tmp_path):
    db = tmp_path / "empty.sqlite3"
    sqlite3.connect(db).close()
    result = audit(db)
    assert result["status"] == "SCHEMA_INCOMPLETE"


def test_join_and_manual_censorship(tmp_path):
    db = tmp_path / "history.sqlite3"
    with sqlite3.connect(db) as con:
        con.executescript("""
            CREATE TABLE decision_samples (
                id INTEGER PRIMARY KEY, sample_key TEXT, captured INTEGER,
                symbol TEXT, direction TEXT, mid REAL, spread REAL, atr REAL,
                regime_features TEXT, entry_features TEXT, base_decision TEXT,
                final_decision TEXT
            );
            CREATE TABLE trade_outcomes (
                trade_key TEXT, sample_key TEXT, symbol TEXT, direction TEXT,
                opened INTEGER, closed INTEGER, net_units REAL, net_r REAL,
                exit_reason TEXT, initial_risk_units REAL, training_status TEXT,
                entry_source TEXT
            );
        """)
        con.execute("INSERT INTO decision_samples VALUES (1,'d1',100,'XAUUSD_l','BUY',4100,0.4,10,'{}','{}','BUY','BUY')")
        con.executemany(
            "INSERT INTO trade_outcomes VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                ("t1","d1","XAUUSD_l","BUY",101,120,0.1,0.5,"DEAL_REASON_TP",0.2,"LEARNABLE","AUTO"),
                ("t2","missing","XAUUSD_l","SELL",130,150,-0.1,-0.5,"DEAL_REASON_CLIENT",0.2,"CENSORED_MANUAL","MANUAL"),
            ],
        )
    result = audit(db)
    assert result["available"]
    assert result["trades"]["matched_decisions"] == 1
    assert result["trades"]["join_rate_pct"] == 50
    assert result["trades"]["manual_exit_count"] == 1
    assert result["training"]["exit_label_eligible"] == 1
    assert result["training"]["ready_for_training"] is False


def test_timestamp_leakage_warning(tmp_path):
    db = tmp_path / "leak.sqlite3"
    with sqlite3.connect(db) as con:
        con.executescript("""
            CREATE TABLE decision_samples (
                id INTEGER PRIMARY KEY, sample_key TEXT, captured INTEGER,
                symbol TEXT, direction TEXT, mid REAL, spread REAL, atr REAL,
                regime_features TEXT, entry_features TEXT, base_decision TEXT
            );
            CREATE TABLE trade_outcomes (
                trade_key TEXT, sample_key TEXT, symbol TEXT, direction TEXT,
                opened INTEGER, closed INTEGER, net_units REAL, net_r REAL,
                exit_reason TEXT, initial_risk_units REAL
            );
            INSERT INTO decision_samples VALUES (1,'d1',200,'XAUUSD_l','BUY',4100,0.4,10,'{}','{}','BUY');
            INSERT INTO trade_outcomes VALUES ('t1','d1','XAUUSD_l','BUY',100,130,0.1,1,'DEAL_REASON_TP',0.1);
        """)
    result = audit(db)
    assert result["trades"]["decision_after_entry"] == 1
    assert any("leakage" in item for item in result["issues"])
