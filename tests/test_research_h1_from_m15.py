import csv
import sqlite3

from ramon.research_h1_from_m15 import aggregate_h1, export_h1


def bars(hour=0):
    return [(hour + 900 * i, 10 + i, 12 + i, 9 + i, 11 + i) for i in range(4)]


def test_aggregate_correct_ohlc_and_skip_partial():
    out, skipped = aggregate_h1(bars() + bars(3600)[:3])
    assert len(out) == 1
    assert len(skipped) == 1
    assert out[0]["time"] == 0
    assert out[0]["open"] == 10
    assert out[0]["high"] == 15
    assert out[0]["low"] == 9
    assert out[0]["close"] == 14


def test_duplicate_or_invalid_bar_not_filled():
    out, skipped = aggregate_h1(bars() + [bars()[1]] + bars(3600))
    assert len(out) == 1
    assert len(skipped) == 1
    invalid = [(0, 10, 9, 11, 10)] + bars()[1:]
    out, skipped = aggregate_h1(invalid)
    assert out == []
    assert len(skipped) == 1


def test_sqlite_read_only_csv_and_report(tmp_path):
    db = tmp_path / "history.sqlite"
    with sqlite3.connect(db) as con:
        con.execute("""CREATE TABLE history_bars (
            symbol TEXT, timeframe TEXT, time INTEGER,
            open REAL, high REAL, low REAL, close REAL)""")
        con.executemany("INSERT INTO history_bars VALUES (?,?,?,?,?,?,?)",
                        [("XAUUSD_l", "M15", *x) for x in bars()])
    before = db.read_bytes()
    result = export_h1(db, symbol="XAUUSD_l",
                       output=tmp_path / "out.csv", report=tmp_path / "quality.json")
    assert result["complete_h1"] == 1
    assert result["incomplete_or_invalid_h1"] == 0
    assert db.read_bytes() == before
    with (tmp_path / "out.csv").open(newline="") as stream:
        assert len(list(csv.DictReader(stream))) == 1
