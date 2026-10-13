from __future__ import annotations

import sqlite3
from types import SimpleNamespace
from unittest.mock import patch

from scripts.reconcile_postgres_m15 import reconcile


def sqlite_fixture(path):
    with sqlite3.connect(path) as db:
        db.execute("""CREATE TABLE history_bars (
            symbol TEXT, timeframe TEXT, time INTEGER, open REAL, high REAL,
            low REAL, close REAL, spread_points INTEGER)""")
        db.execute("INSERT INTO history_bars VALUES (?,?,?,?,?,?,?,?)",
                   ("XAUUSD_l", "M15", 900, 100., 103., 99., 102., 42))


class Cursor:
    def __init__(self):
        self.seen = []
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def executemany(self, sql, rows): self.seen.extend(rows)


class Connection:
    def __init__(self): self.cur = Cursor()
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def cursor(self): return self.cur


def test_reconcile_is_dry_run_by_default(tmp_path):
    path = tmp_path / "history.sqlite3"
    sqlite_fixture(path)
    assert reconcile(path, "", "XAUUSD_l", execute=False)["status"] == "DRY_RUN"


def test_reconcile_upserts_from_sqlite_without_mutating_source(tmp_path):
    path = tmp_path / "history.sqlite3"
    sqlite_fixture(path)
    conn = Connection()
    with patch.dict("sys.modules", {"psycopg": SimpleNamespace(connect=lambda *a, **kw: conn)}):
        result = reconcile(path, "not-a-real-dsn", "XAUUSD_l", execute=True)
    assert result["written"] == 1
    assert conn.cur.seen == [("XAUUSD_l", "M15", 900, 100., 103., 99., 102., 42)]
    with sqlite3.connect(path) as source:
        assert source.execute("SELECT COUNT(*) FROM history_bars").fetchone()[0] == 1
