import json
import sqlite3

from ramon.position_lifecycle_audit import analyze_events, audit_database


def event(seq, kind, sl=None, stage=None, pid="p1", broker_time=1000):
    data = {}
    if sl is not None:
        data["sl"] = sl
    if stage is not None:
        data["tp_stage"] = stage
    return {
        "event_id": f"account:run:{seq}",
        "account_key": "account",
        "position_id": pid,
        "kind": kind,
        "sample_key": "decision",
        "received_utc": 100.0,
        "broker_time_msc": broker_time,
        "body_json": json.dumps({"data": data}),
    }


def test_sl_and_stage_observation_with_equal_batch_receipt_time():
    rows = [
        event(10, "position_change", sl=101, stage=1, broker_time=2000),
        event(2, "position_quote", sl=100, stage=0, broker_time=1000),
        event(11, "position_quote", sl=101, stage=1, broker_time=2200),
        event(12, "telemetry_gap", pid="", broker_time=2300),
    ]
    result = analyze_events(rows)
    assert result["positions_with_events"] == 1
    assert result["telemetry_gap_events"] == 1
    pos = result["positions"][0]
    assert len(pos["observed_sl_transitions"]) == 1
    assert pos["observed_sl_transitions"][0]["from"] == 100
    assert pos["observed_sl_transitions"][0]["to"] == 101
    assert len(pos["observed_tp_stage_transitions"]) == 1


def test_account_scopes_position_identity():
    a = event(1, "position_quote", sl=100)
    b = {**event(2, "position_quote", sl=101), "account_key": "other"}
    assert analyze_events([a, b])["positions_with_events"] == 2


def test_reads_database_without_writing(tmp_path):
    db = tmp_path / "events.sqlite"
    with sqlite3.connect(db) as conn:
        conn.execute("""CREATE TABLE research_events(
            event_id TEXT, account_key TEXT, role TEXT, symbol TEXT,
            kind TEXT, sample_key TEXT, position_id TEXT,
            broker_time_msc INTEGER, received_utc REAL, body_json TEXT)""")
        e = event(1, "position_quote", sl=100)
        conn.execute("INSERT INTO research_events VALUES (?,?,?,?,?,?,?,?,?,?)",
                     (e["event_id"], e["account_key"], "MAIN", "XAUUSD_l",
                      e["kind"], e["sample_key"], e["position_id"],
                      e["broker_time_msc"], e["received_utc"], e["body_json"]))
    before = db.read_bytes()
    result = audit_database(db)
    assert result["positions_with_events"] == 1
    assert db.read_bytes() == before


def test_transient_old_sl_snapshot_not_counted_as_stable_change():
    rows = [
        event(1, "position_quote", sl=4163, broker_time=1000),
        event(2, "position_quote", sl=4163, broker_time=2000),
        event(3, "position_change", sl=4178.8, broker_time=3000),
        event(4, "position_quote", sl=4163, broker_time=3400),
        event(5, "position_change", sl=4178.8, broker_time=5000),
        event(6, "position_quote", sl=4178.8, broker_time=5000),
        event(7, "position_change", sl=4181.45, broker_time=7000),
        event(8, "position_quote", sl=4181.45, broker_time=8000),
    ]
    result = analyze_events(rows)["positions"][0]
    assert len(result["observed_sl_transitions"]) == 4
    assert len(result["stable_observed_sl_transitions"]) == 2
    assert len(result["transient_sl_conflicts"]) == 1
    assert result["unconfirmed_final_sl_candidate"] is None
    assert [(c["from"], c["to"]) for c in result["stable_observed_sl_transitions"]] == [
        (4163, 4178.8), (4178.8, 4181.45)
    ]
