import sqlite3
from ramon.evaluation_lab import evaluate


def test_manual_dashboard_exit_excluded_even_when_entry_is_auto(tmp_path):
    path = tmp_path / "h.db"
    with sqlite3.connect(path) as db:
        db.executescript("""
        CREATE TABLE decision_samples (symbol TEXT, sample_key TEXT, final_decision TEXT);
        CREATE TABLE trade_outcomes (symbol TEXT,sample_key TEXT,trade_key TEXT,
          entry_source TEXT,net_units REAL,net_r REAL,closed REAL,
          manual_intervention TEXT,exit_detail TEXT,exit_reason TEXT);
        INSERT INTO decision_samples VALUES ('XAUUSD_l','k1','BUY');
        INSERT INTO decision_samples VALUES ('XAUUSD_l','k2','SELL');
        INSERT INTO decision_samples VALUES ('XAUUSD_l','k3','SELL');
        INSERT INTO trade_outcomes VALUES
           ('XAUUSD_l','k1','t1','AUTO_RAMON',-11.24,-.3,1,NULL,'manual_dashboard_close',NULL),
           ('XAUUSD_l','k2','t2','AUTO_RAMON',-7.2,-.7,1,NULL,'early_adverse_exit',NULL),
           ('XAUUSD_l','k3','t3','RANGE_AUTO',4.1,.5,1,NULL,NULL,NULL);
        """)
    r = evaluate(path)
    assert r["manual_provenance_audit"]["suspected_count"] == 1
    assert r["manual_provenance_audit"]["suspected"][0]["status"] == "MANUAL_INTERVENTION_SUSPECTED"
    assert r["data_audit"]["exclusion_reasons"]["MANUAL_EXIT_SUSPECTED"] == 1
    assert r["auto_trades"]["candidate_count"] == 2
    assert r["early_exit_replay"]["candidate_trades"] == 1
    assert r["early_exit_replay"]["observed_net_units"] == -7.2
    assert r["early_exit_replay"]["hypothetical_net_units"] is None
    assert r["early_exit_replay"]["per_trade"][0]["replay_status"] == "NOT_EVALUATED"
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM trade_outcomes").fetchone()[0] == 3
