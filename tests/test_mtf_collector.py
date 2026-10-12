from __future__ import annotations

import sqlite3
from pathlib import Path

from ramon.mtf_collector import poll


def test_import_and_reimport_only_changed_snapshot(tmp_path: Path):
    files = tmp_path / "common"
    files.mkdir()
    db = tmp_path / "history.sqlite3"
    source = files / "RamonLive_XAUUSD_l_M1.csv"
    source.write_text("time,open,high,low,close,spread_points\n"
                      "1791800000,4000,4002,3999,4001,40\n", encoding="utf-8")
    seen = {}
    poll(files, db, "XAUUSD_l", seen)
    assert "M1" in seen and "M5" not in seen
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT COUNT(*) FROM history_bars WHERE timeframe='M1'").fetchone()[0] == 1
    poll(files, db, "XAUUSD_l", seen)
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT COUNT(*) FROM history_bars WHERE timeframe='M1'").fetchone()[0] == 1


def test_invalid_snapshot_is_not_marked_imported(tmp_path: Path):
    files = tmp_path / "common"
    files.mkdir()
    db = tmp_path / "history.sqlite3"
    source = files / "RamonLive_XAUUSD_l_M5.csv"
    source.write_text("not,a,valid,csv\n", encoding="utf-8")
    seen = {}
    poll(files, db, "XAUUSD_l", seen)
    assert "M5" not in seen
