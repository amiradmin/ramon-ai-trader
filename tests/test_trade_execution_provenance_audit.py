import csv
import sqlite3

from ramon.trade_execution_provenance_audit import (
    audit_execution, export_execution_audit,
)


def decision(key, *, direction="BUY", final="WAIT"):
    return dict(symbol="XAUUSD_l", sample_key=key, captured=10800,
                signal_bar_time=20700, direction=direction,
                base_decision="BUY", final_decision=final)


def trade(key, trade_key, *, direction="SELL", source="RANGE"):
    return dict(symbol="XAUUSD_l", sample_key=key, trade_key=trade_key,
                opened=10830, direction=direction, entry_source=source,
                trade_role="MAIN", entry_magic=26092212,
                entry_ea_version="0.55")


def test_wait_is_unresolved_even_with_entry_source_and_event():
    rows, report = audit_execution(
        [decision("a")], [trade("a", "T1")],
        [{"symbol": "XAUUSD_l", "sample_key": "a",
          "kind": "position_quote"}],
        broker_utc_offset_seconds=10800)
    assert rows[0]["classification"] == "FINAL_FIELD_WAIT"
    assert rows[0]["execution_provenance"] == "UNRESOLVED_EXECUTION_WITH_CONFLICT"
    assert rows[0]["entry_source"] == "RANGE"
    assert rows[0]["event_count_same_sample"] == 1
    assert report["unresolved_conflicts"] == 1
    assert report["conflict_entry_provenance_groups"][0]["trades"] == 1


def test_final_match_not_authorization_and_absent_match():
    decisions = [decision("a", final="SELL"),
                 decision("b", final="BUY")]
    trades = [trade("a", "T1"), trade("b", "T2"), trade("missing", "T3")]
    rows, report = audit_execution(
        decisions, trades, broker_utc_offset_seconds=10800)
    by_id = {r["trade_key"]: r for r in rows}
    assert by_id["T1"]["execution_provenance"] == (
        "STORED_FINAL_AGREEMENT_EXECUTION_UNVERIFIED")
    assert by_id["T2"]["classification"] == "NO_RECORDED_DIRECTION_MATCH"
    assert by_id["T3"]["classification"] == "UNMATCHED_DECISION"
    assert report["unresolved_conflicts"] == 1


def test_noncausal_trade_is_never_promoted():
    d = decision("a", final="SELL")
    t = trade("a", "T1")
    t["opened"] = 100
    rows, _ = audit_execution(
        [d], [t], broker_utc_offset_seconds=10800)
    assert rows[0]["classification"] == "NONCAUSAL_TIME"
    assert rows[0]["execution_provenance"] == "UNRESOLVED_IDENTITY_OR_CAUSALITY"


def test_export_read_only_and_missing_optional_telemetry(tmp_path):
    db = tmp_path / "history.sqlite3"
    with sqlite3.connect(db) as conn:
        conn.execute("""CREATE TABLE decision_samples (
            sample_key TEXT, symbol TEXT, captured INTEGER, signal_bar_time INTEGER,
            direction TEXT, base_decision TEXT, final_decision TEXT)""")
        conn.execute("""CREATE TABLE trade_outcomes (
            trade_key TEXT, sample_key TEXT, symbol TEXT, opened INTEGER,
            direction TEXT)""")
        conn.execute("""CREATE TABLE research_events (
            symbol TEXT,sample_key TEXT,kind TEXT)""")
        conn.execute("INSERT INTO decision_samples VALUES(?,?,?,?,?,?,?)",
                     ("a", "XAUUSD_l", 10800, 20700, "BUY", "BUY", "WAIT"))
        conn.execute("INSERT INTO trade_outcomes VALUES(?,?,?,?,?)",
                     ("T1", "a", "XAUUSD_l", 10830, "SELL"))
        conn.execute("INSERT INTO research_events VALUES(?,?,?)",
                     ("XAUUSD_l", "a", "position_change"))
    before = db.read_bytes()
    output = tmp_path / "rows.csv"
    report = tmp_path / "summary.json"
    summary = export_execution_audit(
        db, symbol="XAUUSD_l", broker_utc_offset_seconds=10800,
        output=output, report=report)
    assert db.read_bytes() == before
    assert summary["unresolved_conflicts"] == 1
    with output.open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert rows[0]["entry_source"] == ""
    assert rows[0]["event_kinds_same_sample"] == "POSITION_CHANGE:1"
