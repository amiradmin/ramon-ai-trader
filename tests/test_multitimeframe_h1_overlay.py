import csv
import sqlite3

import pytest

from ramon.multitimeframe_h1_overlay import load_research_h1, read_context_with_overlay


def prepare(tmp_path):
    db = tmp_path / "history.sqlite"
    with sqlite3.connect(db) as conn:
        conn.execute("""CREATE TABLE history_bars(
            symbol TEXT, timeframe TEXT, time INTEGER,
            open REAL, high REAL, low REAL, close REAL)""")
        for tf, interval in (("M5", 300), ("M15", 900)):
            for t in range(0, 10800, interval):
                conn.execute("INSERT INTO history_bars VALUES(?,?,?,?,?,?,?)",
                             ("XAUUSD_l", tf, t, 10, 12, 9, 11))
    path = tmp_path / "h1.csv"
    with path.open("w", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["time", "open", "high", "low", "close", "source"])
        for t in (0, 3600):
            writer.writerow([t, 10, 12, 9, 11, "M15_AGGREGATED_RESEARCH_ONLY"])
    return db, path


def test_overlay_returns_only_completed_context(tmp_path):
    db, csv_path = prepare(tmp_path)
    old_bytes = db.read_bytes()
    report = read_context_with_overlay(
        db, csv_path, symbol="XAUUSD_l", decision_utc=7200)
    assert report["ready"]
    assert report["timeframes"]["H1"]["bars"][-1]["open_utc"] == 3600
    assert report["timeframes"]["M5"]["bars"][-1]["open_utc"] == 6900
    assert db.read_bytes() == old_bytes


def test_missing_h1_fails_closed(tmp_path):
    db, csv_path = prepare(tmp_path)
    report = read_context_with_overlay(db, csv_path, symbol="XAUUSD_l",
                                       decision_utc=10800)
    assert not report["ready"]
    assert report["timeframes"]["H1"]["bars"] == []


def test_bad_provenance_and_duplicate_fails(tmp_path):
    db, csv_path = prepare(tmp_path)
    with csv_path.open("a") as stream:
        stream.write("3600,10,12,9,11,M15_AGGREGATED_RESEARCH_ONLY\n")
    with pytest.raises(ValueError, match="duplicate"):
        load_research_h1(csv_path)
    with csv_path.open("w") as stream:
        stream.write("time,open,high,low,close,source\n")
        stream.write("0,10,12,9,11,NATIVE_H1\n")
    with pytest.raises(ValueError, match="provenance"):
        load_research_h1(csv_path)


def test_unfinished_h1_never_used(tmp_path):
    db, csv_path = prepare(tmp_path)
    report = read_context_with_overlay(db, csv_path, symbol="XAUUSD_l",
                                       decision_utc=7199)
    assert report["timeframes"]["H1"]["bars"] == []
    assert not report["ready"]
