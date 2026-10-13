from __future__ import annotations

import csv
import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from ramon.mtf_collector import mirror_snapshot_postgres


class FakeCursor:
    def __init__(self):
        self.rows = None
        self.sql = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def executemany(self, sql, rows):
        self.sql = sql
        self.rows = rows


class FakeConn:
    def __init__(self):
        self.cursor_instance = FakeCursor()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def cursor(self):
        return self.cursor_instance


def test_pg_mtf_shadow_validates_and_upserts(tmp_path):
    src = tmp_path / "bars.csv"
    with src.open("w", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(("time", "open", "high", "low", "close", "spread_points"))
        writer.writerow((10920, 100, 103, 99, 102, 42))
    fake = FakeConn()
    with patch.dict(sys.modules, {"psycopg": SimpleNamespace(connect=lambda *a, **k: fake)}):
        result = mirror_snapshot_postgres(src, "XAUUSD_l", "M1", 3, "postgresql://test", now=11000)
    assert result["valid"] == 1
    assert fake.cursor_instance.rows[0] == (
        "XAUUSD_l", "M1", 120, 10920, 10800, 100., 103., 99., 102., 42
    )
    assert "ramon_migration.mtf_utc_bars" in fake.cursor_instance.sql
    assert "ON CONFLICT" in fake.cursor_instance.sql


def test_pg_mtf_shadow_rejects_future_bar_before_connect(tmp_path):
    src = tmp_path / "bars.csv"
    src.write_text("time,open,high,low,close,spread_points\\n10920,100,103,99,102,42\n")
    with patch.dict(sys.modules, {"psycopg": SimpleNamespace(connect=lambda *a, **k: pytest.fail("connected"))}):
        with pytest.raises(ValueError):
            mirror_snapshot_postgres(src, "XAUUSD_l", "M1", 3, "postgresql://test", now=100)
