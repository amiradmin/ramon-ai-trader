from __future__ import annotations
import sqlite3
from pathlib import Path

import pytest
from ramon.mtf_collector import poll, import_snapshot


def test_canonical_utc_and_raw_provenance(tmp_path: Path):
    common = tmp_path / "common"
    common.mkdir()
    db = tmp_path / "history.sqlite3"
    # At UTC 06:19, broker's M1 clock is UTC+3.
    broker = 1791796680
    source = common / "RamonLive_XAUUSD_l_M1.csv"
    source.write_text("time,open,high,low,close,spread_points\n"
                      f"{broker},4182,4184,4181,4183,42\n", encoding="utf-8")
    seen = {}
    poll(common, db, "XAUUSD_l", seen, offset_hours=3)
    assert "M1" in seen
    with sqlite3.connect(db) as con:
        result = con.execute("SELECT time,broker_time,offset_seconds FROM mtf_utc_bars").fetchone()
        assert result == (broker - 10800, broker, 10800)
        assert con.execute("SELECT COUNT(*) FROM mtf_utc_bars").fetchone()[0] == 1
        # Existing production candle history remains untouched.
        assert con.execute("SELECT name FROM sqlite_master WHERE name='history_bars'").fetchone() is None
    poll(common, db, "XAUUSD_l", seen, offset_hours=3)


def test_wrong_offset_rejects_future_bars(tmp_path: Path):
    source = tmp_path / "bad.csv"
    source.write_text("time,open,high,low,close,spread_points\n"
                      "1791796680,4182,4184,4181,4183,42\n", encoding="utf-8")
    with pytest.raises(ValueError, match="future/unclosed"):
        import_snapshot(tmp_path / "db.sqlite3", source, "XAUUSD_l", "M1", 0,
                        now=1791782400)


def test_invalid_snapshot_is_not_imported(tmp_path: Path):
    common = tmp_path / "common"
    common.mkdir()
    db = tmp_path / "history.sqlite3"
    source = common / "RamonLive_XAUUSD_l_M5.csv"
    source.write_text("not,a,valid,csv\n", encoding="utf-8")
    seen = {}
    poll(common, db, "XAUUSD_l", seen, offset_hours=3)
    assert "M5" not in seen
