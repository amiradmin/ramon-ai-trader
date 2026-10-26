import json
import sqlite3

from ramon.daily_decision_report import build_report


def test_daily_decision_report_groups_repeated_snapshots(tmp_path):
    db = tmp_path / "decisions.sqlite3"
    with sqlite3.connect(db) as con:
        con.execute("""CREATE TABLE decision_samples (
            id INTEGER PRIMARY KEY, captured INTEGER, symbol TEXT,
            signal_bar_time INTEGER, final_decision TEXT,
            base_decision TEXT, model_metadata TEXT, sample_key TEXT
        )""")
        from datetime import datetime, timezone
        ts = int(datetime(2026, 10, 12, 12, tzinfo=timezone.utc).timestamp())
        for i, decision, reason in [
            (0, "WAIT", "insufficient_model_edge"),
            (1, "WAIT", "insufficient_model_edge"),
            (2, "SELL", "confirmed"),
        ]:
            meta = {"direction": "SELL", "decision_audit": {
                "base": {"decision": "WAIT"},
                "final": {"decision": decision, "reason": reason}}}
            con.execute(
                "INSERT INTO decision_samples(captured,symbol,signal_bar_time,final_decision,base_decision,model_metadata,sample_key) VALUES(?,?,?,?,?,?,?)",
                (ts + i*5, "XAUUSD_l", ts, decision, "WAIT", json.dumps(meta), f"key{i}"),
            )
    report = build_report(db, day="2026-10-12")
    assert report["recorded_requests"] == 3
    assert report["unique_saved_decision_ids"] == 3
    assert report["signal_bar_direction_opportunities"] == 1
    assert report["opportunities_with_any_wait"] == 1
    assert report["wait_reasons_by_request"]["insufficient_model_edge"] == 2
    assert report["wait_reasons_by_opportunity"]["insufficient_model_edge"] == 1
    assert build_report(db, day="2026-10-11")["recorded_requests"] == 0
