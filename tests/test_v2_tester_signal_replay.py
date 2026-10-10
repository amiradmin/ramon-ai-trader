"""Causality tests for offline decision replay; not a Strategy Tester fill test."""
import sqlite3

from ramon.v2_tester_signal_replay import replay


def test_filters_future_stale_and_wait(tmp_path):
    path=tmp_path / "history.sqlite3"
    with sqlite3.connect(path) as con:
        con.execute("""CREATE TABLE decision_samples (
            captured INTEGER, symbol TEXT, signal_bar_time INTEGER,
            direction TEXT, final_decision TEXT)""")
        con.executemany("INSERT INTO decision_samples VALUES (?,?,?,?,?)", [
            (1000, "XAUUSD_l", 0, "BUY", "BUY"),
            (2000, "XAUUSD_l", 900, "BUY", "BUY"),
            (3000, "XAUUSD_l", 2100, "SELL", "SELL"),
            (4000, "XAUUSD_l", 3100, "BUY", "WAIT"),
            (5000, "EURUSD", 4100, "SELL", "SELL"),
        ])
    result=replay(path, max_signal_age=150)
    assert result["rows"] == 4
    assert result["causal_signals"] == 2
    assert result["rejected_reasons"]["stale_signal_bar"] == 1
    assert result["rejected_reasons"]["wait_or_unknown"] == 1
    assert result["profit_claim"] is False
    assert result["broker_trades"] is False


def test_future_bar_is_rejected(tmp_path):
    path=tmp_path / "history.sqlite3"
    with sqlite3.connect(path) as con:
        con.execute("CREATE TABLE decision_samples (captured INTEGER, symbol TEXT, signal_bar_time INTEGER, direction TEXT)")
        con.execute("INSERT INTO decision_samples VALUES (100, 'XAUUSD_l', 50, 'BUY')")
    report = replay(path)
    assert report["causal_signals"] == 0
    assert report["rejected_reasons"]["future_or_incomplete_bar"] == 1
