import sqlite3

import pytest

from ramon.tick_trade_coverage import coverage, tick_window


def test_tick_window_checks_order_and_quote_integrity(tmp_path):
    path = tmp_path / "ticks.csv"
    path.write_text("time_ms,bid,ask\n1000,10,11\n2000,10.5,11\n")
    assert tick_window(path) == {"first_ms": 1000, "last_ms": 2000, "tick_rows": 2}
    path.write_text("time_ms,bid,ask\n2000,10,11\n1000,10,11\n")
    with pytest.raises(ValueError):
        tick_window(path)
    path.write_text("time_ms,bid,ask\n1000,11,10\n")
    with pytest.raises(ValueError):
        tick_window(path)


def test_coverage_excludes_outside_and_requires_exact_join(tmp_path):
    db = tmp_path / "history.sqlite3"
    with sqlite3.connect(db) as con:
        con.execute("""CREATE TABLE trade_outcomes (
            trade_key TEXT, sample_key TEXT, symbol TEXT, opened INTEGER,
            closed INTEGER, direction TEXT, actual_fill_price REAL,
            net_r REAL, training_status TEXT, exit_reason TEXT)""")
        con.execute("""CREATE TABLE decision_samples (
            sample_key TEXT, symbol TEXT, captured INTEGER,
            stop_distance REAL, target_distance REAL, target_structure TEXT)""")
        con.execute("""CREATE TABLE target_outcomes (
            sample_key TEXT, trade_key TEXT, tp1 REAL, tp2 REAL, tp3 REAL)""")
        con.executemany("INSERT INTO trade_outcomes VALUES (?,?,?,?,?,?,?,?,?,?)", [
            ("a", "da", "XAUUSD_l", 10, 12, "BUY", 100.0, 1.0, "LEARNABLE", "TP"),
            ("b", "missing", "XAUUSD_l", 15, 17, "SELL", None, -1.0, "LEARNABLE", "SL"),
            ("c", "dc", "XAUUSD_l", 19, 30, "BUY", 100.0, 1.0, "LEARNABLE", "TP"),
        ])
        con.execute("INSERT INTO decision_samples VALUES (?,?,?,?,?,?)",
                    ("da", "XAUUSD_l", 9, 1.0, 2.0, "{}"))
        con.execute("INSERT INTO target_outcomes VALUES (?,?,?,?,?)",
                    ("da", "a", 101.0, 102.0, 103.0))
    report = coverage(db, first_ms=9000, last_ms=20000)
    assert report["total_trades_for_symbol"] == 3
    assert report["trades_fully_within_tick_window"] == 2
    assert report["counts"]["outside_complete_tick_window"] == 1
    assert report["counts"]["decision_link_valid"] == 1
    assert report["counts"]["missing_decision_link"] == 1
    assert report["counts"]["matched_target_outcome"] == 1
    assert report["counts"]["actual_fill_price_missing"] == 1
