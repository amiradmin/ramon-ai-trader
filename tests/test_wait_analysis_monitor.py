"""Read-only WAIT dashboard contract and minimal SQLite fixture."""
import json
import sqlite3
from ramon.monitor import wait_analysis_report

def test_wait_report_categorizes_without_writing(tmp_path):
    path = tmp_path / "history.sqlite3"
    with sqlite3.connect(path) as con:
        con.execute("""CREATE TABLE decision_samples (
            id INTEGER PRIMARY KEY, symbol TEXT, sample_key TEXT,
            captured INTEGER, final_decision TEXT, model_metadata TEXT)""")
        metadata = {"decision_audit": {
            "base": {"reason":"adverse_intrabar_timing","signal_strength":0.23,"minimum_strength":0.2},
            "final": {"reason":"ai_engine_v2_market_hazard","candidate_direction":"BUY",
                      "market_direction":"SELL","market_state":"REGIME_TRANSITION",
                      "data_quality_shadow_reasons":["m15_gap"]}}}
        con.execute("INSERT INTO decision_samples VALUES (1,?,?,?,?,?)",
                    ("XAUUSD_l","sample-1",1791881000,"WAIT",json.dumps(metadata)))
        con.execute("INSERT INTO decision_samples VALUES (2,?,?,?,?,?)",
                    ("XAUUSD_l","sample-2",1791881001,"BUY",json.dumps(metadata)))
    report = wait_analysis_report(str(path), "XAUUSD_l")
    assert report["available"] is True
    assert report["sampled_waits"] == 1
    assert report["direction_conflicts"] == 1
    assert report["reasons"]["ai_engine_v2_market_hazard"] == 1
    assert report["quality_shadow_reasons"]["m15_gap"] == 1
    assert report["counterfactual"]["status"] == "NOT_EVALUATED"
    with sqlite3.connect(path) as con:
        assert con.execute("SELECT COUNT(*) FROM decision_samples").fetchone()[0] == 2

def test_wait_report_missing_db(tmp_path):
    assert wait_analysis_report(tmp_path / "missing.sqlite3", "XAUUSD_l")["available"] is False
