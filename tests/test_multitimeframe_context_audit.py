import sqlite3

from ramon.multitimeframe_context_audit import context_at, read_context, TIMEFRAME_SECONDS


def make_db(path):
    with sqlite3.connect(path) as db:
        db.execute("""CREATE TABLE history_bars(
            symbol TEXT, timeframe TEXT, time INTEGER,
            open REAL, high REAL, low REAL, close REAL)""")
        for tf, secs in TIMEFRAME_SECONDS.items():
            for start in range(0, 4 * 3600, secs):
                db.execute("INSERT INTO history_bars VALUES (?,?,?,?,?,?,?)",
                           ("XAUUSD_l", tf, start, 10, 12, 9, 11))


def test_closed_bars_only_and_boundary(tmp_path):
    path = tmp_path / "bars.db"
    make_db(path)
    # At 7200 all TFs have 2 completed bars; H1 open 7200 isn't complete.
    result = read_context(path, symbol="XAUUSD_l", decision_utc=7200)
    assert result["ready"]
    for tf, seconds in TIMEFRAME_SECONDS.items():
        bars = result["timeframes"][tf]["bars"]
        assert len(bars) == 2
        assert bars[-1]["open_utc"] + seconds <= 7200


def test_missing_or_gap_fails_closed(tmp_path):
    path = tmp_path / "bars.db"
    make_db(path)
    with sqlite3.connect(path) as db:
        db.execute("DELETE FROM history_bars WHERE timeframe='H1' AND time=3600")
    result = read_context(path, symbol="XAUUSD_l", decision_utc=7200)
    assert result["ready"] is False
    assert result["timeframes"]["H1"]["ready"] is False
    assert result["timeframes"]["H1"]["bars"] == []


def test_no_future_bar_and_read_only(tmp_path):
    path = tmp_path / "bars.db"
    make_db(path)
    before = path.read_bytes()
    result = read_context(path, symbol="XAUUSD_l", decision_utc=1000)
    assert result["ready"] is False
    assert all(not val["bars"] or all(
        bar["open_utc"] + TIMEFRAME_SECONDS[tf] <= 1000 for bar in val["bars"])
        for tf, val in result["timeframes"].items())
    assert path.read_bytes() == before


def test_invalid_request(tmp_path):
    path = tmp_path / "bars.db"
    make_db(path)
    with sqlite3.connect(path) as conn:
        import pytest
        with pytest.raises(ValueError):
            context_at(conn, symbol="XAUUSD_l", decision_utc=0)
