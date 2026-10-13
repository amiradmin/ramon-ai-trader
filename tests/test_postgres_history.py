from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from ramon.postgres_history import load_bars_postgres


class Cursor:
    def __init__(self, rows):
        self.rows = rows
        self.sql = None
        self.params = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql, params):
        self.sql, self.params = sql, params

    def fetchall(self):
        return self.rows


class Conn:
    def __init__(self, rows):
        self.cur = Cursor(rows)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def cursor(self):
        return self.cur


def test_read_pg_m15_matches_existing_contract():
    fake = Conn([(100, 12.0, 14.0, 11.0, 13.0, 42),
                 (1000, 13.0, 15.0, 12.0, 14.0, 43)])
    with patch.dict(sys.modules, {"psycopg": SimpleNamespace(connect=lambda *a, **k: fake)}):
        bars, spreads = load_bars_postgres("postgresql://unused", "XAUUSD_l")
    assert [bar.time for bar in bars] == [100, 1000]
    assert spreads == (42, 43)
    assert fake.cur.params == ("XAUUSD_l",)
    assert "ORDER BY time" in fake.cur.sql


def test_empty_postgres_history_errors():
    fake = Conn([])
    with patch.dict(sys.modules, {"psycopg": SimpleNamespace(connect=lambda *a, **k: fake)}):
        with pytest.raises(ValueError, match="no XAUUSD_l/M15"):
            load_bars_postgres("postgresql://unused")


def test_missing_dsn_rejected():
    with pytest.raises(ValueError, match="DSN"):
        load_bars_postgres("")
