import csv
import sqlite3

import pytest

from ramon.trade_direction_provenance_audit import (
    classify, export_direction_audit, inspect_direction_provenance,
)


def decision(sample, direction="BUY", base="BUY", final="BUY",
             captured=10800, signal=9900):
    return dict(sample_key=sample, symbol="XAUUSD_l", direction=direction,
                base_decision=base, final_decision=final,
                captured=captured, signal_bar_time=signal + 10800)


def trade(sample, key, direction="SELL", opened=10820):
    return dict(sample_key=sample, symbol="XAUUSD_l", trade_key=key,
                direction=direction, opened=opened)


def test_final_direction_agreement_is_not_automatic_recovery():
    row = classify(trade("a", "1"), decision("a", final="SELL"), offset=10800)
    assert row["classification"] == "FINAL_FIELD_MATCHES_TRADE"
    assert row["final_agrees_with_trade"]
    assert row["signal_bar_utc"] == 9900


def test_base_only_and_no_match_and_missing_final():
    assert classify(trade("a", "1"), decision("a", base="SELL", final="BUY"),
                    offset=10800)["classification"] == "BASE_FIELD_MATCHES_TRADE_ONLY"
    assert classify(trade("a", "1"), decision("a", final="BUY"),
                    offset=10800)["classification"] == "NO_RECORDED_DIRECTION_MATCH"
    assert classify(trade("a", "1"), decision("a", final=None),
                    offset=10800)["classification"] == "FINAL_FIELD_MISSING"


def test_noncausal_not_misclassified_as_valid_final_match():
    row = classify(trade("a", "1", opened=10700),
                   decision("a", final="SELL"), offset=10800)
    assert row["classification"] == "NONCAUSAL_TIME"
    assert row["final_agrees_with_trade"] is False


def test_summary_counts_only_actual_mismatches_and_exact_join():
    decisions = [
        decision("a", final="SELL"),
        decision("b", direction="SELL", base="SELL", final="SELL"),
        decision("c", final="WAIT"),
    ]
    trades = [trade("a", "T1"), trade("b", "T2"),
              trade("c", "T3"), trade("absent", "T4")]
    rows, report = inspect_direction_provenance(
        decisions, trades, broker_utc_offset_seconds=10800)
    assert len(rows) == 4
    assert report["direction_mismatches_total"] == 2
    assert report["mismatched_direction_counts"] == {
        "FINAL_FIELD_MATCHES_TRADE": 1, "FINAL_FIELD_WAIT": 1}
    assert report["classification_counts"]["UNMATCHED_DECISION"] == 1
    assert report["classification_counts"]["RECORDED_DIRECTION_MATCH"] == 1


def test_sqlite_export_read_only(tmp_path):
    db = tmp_path / "test.db"
    with sqlite3.connect(db) as con:
        con.execute("""CREATE TABLE decision_samples(
            sample_key TEXT, symbol TEXT, captured INTEGER,
            signal_bar_time INTEGER, direction TEXT, base_decision TEXT,
            final_decision TEXT)""")
        con.execute("""CREATE TABLE trade_outcomes(
            trade_key TEXT, sample_key TEXT, symbol TEXT, opened INTEGER,
            direction TEXT)""")
        d = decision("x", final="SELL")
        con.execute("INSERT INTO decision_samples VALUES(?,?,?,?,?,?,?)",
                    tuple(d[k] for k in ("sample_key","symbol","captured",
                                          "signal_bar_time","direction",
                                          "base_decision","final_decision")))
        t = trade("x", "T1")
        con.execute("INSERT INTO trade_outcomes VALUES(?,?,?,?,?)",
                    tuple(t[k] for k in ("trade_key","sample_key","symbol",
                                          "opened","direction")))
    original = db.read_bytes()
    output, report = tmp_path / "rows.csv", tmp_path / "summary.json"
    summary = export_direction_audit(
        db, symbol="XAUUSD_l", broker_utc_offset_seconds=10800,
        output=output, report=report)
    assert summary["direction_mismatches_total"] == 1
    assert db.read_bytes() == original
    with output.open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert rows[0]["classification"] == "FINAL_FIELD_MATCHES_TRADE"


def test_invalid_offset_rejected():
    with pytest.raises(ValueError):
        inspect_direction_provenance([], [], broker_utc_offset_seconds=123)
