import sqlite3

from ramon.execution_audit import (
    bar_spread_coverage,
    estimate_point,
    load_joined,
    sell_exit_side_sensitivity,
    timing_audit,
)


def make_db():
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript(
        """
        CREATE TABLE decision_samples (
            sample_key TEXT, symbol TEXT, captured INTEGER, quote_time INTEGER,
            signal_bar_time INTEGER, mid REAL, spread REAL,
            stop_distance REAL, target_distance REAL
        );
        CREATE TABLE trade_outcomes (
            trade_key TEXT, sample_key TEXT, symbol TEXT, direction TEXT,
            opened INTEGER, closed INTEGER,
            opened_utc_offset_seconds INTEGER,
            actual_fill_price REAL
        );
        CREATE TABLE history_bars (
            symbol TEXT, timeframe TEXT, time INTEGER,
            open REAL, high REAL, low REAL, close REAL,
            spread_points INTEGER
        );
        """
    )
    return con


def test_timing_audit_detects_alignment_and_quote_coverage():
    con = make_db()
    con.execute(
        "INSERT INTO decision_samples VALUES (?,?,?,?,?,?,?,?,?)",
        ("a", "XAUUSD_l", 1000, 11799, 10800, 100.0, .4, 2.0, 4.0),
    )
    con.execute(
        "INSERT INTO trade_outcomes VALUES (?,?,?,?,?,?,?,?)",
        ("t1", "a", "XAUUSD_l", "BUY", 11802, 20000, 10800, 100.25),
    )
    rows = load_joined(con, "XAUUSD_l")
    audit = timing_audit(rows)
    assert audit["trades"] == 1
    assert audit["canonical_coverage"] == 1
    assert audit["quote_coverage"] == 1
    assert audit["opened_after_capture"] == 1
    assert audit["opened_after_quote"] == 1
    assert audit["signal_before_open"] == 1


def test_estimate_point_uses_spread_divided_by_points():
    con = make_db()
    con.execute(
        "INSERT INTO decision_samples VALUES (?,?,?,?,?,?,?,?,?)",
        ("a", "XAUUSD_l", 1000, 999, 900, 100.0, .42, 2.0, 4.0),
    )
    con.execute(
        "INSERT INTO history_bars VALUES (?,?,?,?,?,?,?,?)",
        ("XAUUSD_l", "M15", 900, 100, 101, 99, 100, 42),
    )
    point, count = estimate_point(con, "XAUUSD_l")
    assert count == 1
    assert abs(point - .01) < 1e-12


def test_sell_ask_proxy_can_change_raw_tp_into_sl():
    con = make_db()
    con.execute(
        "INSERT INTO decision_samples VALUES (?,?,?,?,?,?,?,?,?)",
        ("a", "XAUUSD_l", 1000, 999, 900, 100.2, .4, 1.0, 1.0),
    )
    con.execute(
        "INSERT INTO trade_outcomes VALUES (?,?,?,?,?,?,?,?)",
        ("t1", "a", "XAUUSD_l", "SELL", 1000, 3000, 0, 100.0),
    )
    # SELL entry = bid = 100.0, stop 101.0, target 99.0.
    # Raw bar reaches target without stop, but Ask proxy with +0.5 reaches stop.
    con.execute(
        "INSERT INTO history_bars VALUES (?,?,?,?,?,?,?,?)",
        ("XAUUSD_l", "M15", 1800, 100.0, 100.6, 98.8, 99.2, 50),
    )
    rows = load_joined(con, "XAUUSD_l")
    out = sell_exit_side_sensitivity(con, rows, "XAUUSD_l", point=.01, max_bars=1)
    assert out["eligible_sell"] == 1
    assert out["compared"] == 1
    assert out["changed_exit"] == 1
    assert out["raw_tp_adjusted_sl"] == 1


def test_bar_spread_coverage_counts_nonzero_rows():
    con = make_db()
    con.executemany(
        "INSERT INTO history_bars VALUES (?,?,?,?,?,?,?,?)",
        [
            ("XAUUSD_l", "M15", 0, 1, 1, 1, 1, 0),
            ("XAUUSD_l", "M15", 900, 1, 1, 1, 1, 42),
        ],
    )
    nonzero, total, pct = bar_spread_coverage(con, "XAUUSD_l")
    assert (nonzero, total, pct) == (1, 2, 50.0)



def test_timing_audit_excludes_rows_without_utc_offset_from_canonical_checks():
    con = make_db()
    con.execute(
        "INSERT INTO decision_samples VALUES (?,?,?,?,?,?,?,?,?)",
        ("a", "XAUUSD_l", 1000, 999, 900, 100.0, .4, 2.0, 4.0),
    )
    con.execute(
        "INSERT INTO trade_outcomes VALUES (?,?,?,?,?,?,?,?)",
        ("t1", "a", "XAUUSD_l", "BUY", 11800, 12000, None, None),
    )
    audit = timing_audit(load_joined(con, "XAUUSD_l"))
    assert audit["trades"] == 1
    assert audit["canonical_coverage"] == 0
    assert audit["capture_to_open"] == []
