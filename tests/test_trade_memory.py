import sqlite3
from datetime import datetime, timezone

import pytest

from ramon.trade_memory import _utc_epoch, memory_snapshot


def make_db(path):
    with sqlite3.connect(path) as db:
        db.execute("""CREATE TABLE decision_samples (
            sample_key TEXT PRIMARY KEY, symbol TEXT, captured INTEGER,
            final_decision TEXT, base_decision TEXT, bundle_id TEXT)""")
        db.execute("""CREATE TABLE trade_outcomes (
            trade_key TEXT PRIMARY KEY, sample_key TEXT, symbol TEXT,
            opened INTEGER, closed INTEGER, net_r REAL, net_units REAL,
            direction TEXT, training_status TEXT, entry_ea_version TEXT)""")
        for key, captured, opened, closed, net in [
            ("a", 100, 110, 150, 2.0),
            ("b", 130, 160, 210, -1.0),
            ("future", 170, 180, 300, 99.0),
            ("late_decision", 220, 190, 195, 90.0),
        ]:
            db.execute(
                "INSERT INTO decision_samples VALUES (?,?,?,?,?,?)",
                (key, "XAUUSD_l", captured, "BUY", "BUY", "bundle"),
            )
            db.execute(
                "INSERT INTO trade_outcomes VALUES (?,?,?,?,?,?,?,?,?,?)",
                (key, key, "XAUUSD_l", opened, closed, net, net,
                 "BUY", "LEARNABLE", "0.55"),
            )
        db.execute(
            "INSERT INTO trade_outcomes VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("unmatched", "missing", "XAUUSD_l", 100, 120,
             500, 500, "BUY", "LEARNABLE", "0.55"),
        )


def test_point_in_time_excludes_future_and_unmatched(tmp_path):
    path = tmp_path / "trades.sqlite3"
    make_db(path)
    earlier = memory_snapshot(path, as_of=200, minimum_samples=2)
    assert earlier["matched_closed_trades"] == 1
    assert earlier["overall_net_r"]["net"] == 2.0
    assert not earlier["by_direction"]["BUY"]["eligible_for_review"]
    later = memory_snapshot(path, as_of=250, minimum_samples=2)
    assert later["matched_closed_trades"] == 2
    assert later["overall_net_r"]["net"] == 1.0
    assert later["by_direction"]["BUY"]["eligible_for_review"]


def test_read_only_does_not_change_source(tmp_path):
    path = tmp_path / "trades.sqlite3"
    make_db(path)
    before = path.read_bytes()
    memory_snapshot(path, as_of=250)
    assert path.read_bytes() == before


def test_timezone_and_invalid_arguments(tmp_path):
    assert _utc_epoch("1970-01-01T01:00:00+01:00") == 0
    with pytest.raises(ValueError):
        _utc_epoch("2026-10-10T09:00:00")
    with pytest.raises(ValueError):
        memory_snapshot(tmp_path / "missing.db", as_of=-1)
