from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch
import sys

from ramon.postgres_market_shadow import mirror_market_postgres
from ramon.server import persist_market_safely


class Cursor:
    def __init__(self):
        self.sql = None
        self.rows = None
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def executemany(self, sql, rows):
        self.sql, self.rows = sql, rows


class Conn:
    def __init__(self): self.cur = Cursor()
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def cursor(self): return self.cur


def market():
    bars = [SimpleNamespace(time=900, open=100, high=102, low=99, close=101),
            SimpleNamespace(time=1800, open=101, high=103, low=100, close=102)]
    return SimpleNamespace(symbol="XAUUSD_l", timeframe="M15", ask=102.42,
                           bid=102.0, point=0.01, bars=bars)


def test_m15_postgres_mirror_upsert_preserves_old_spread():
    db = Conn()
    with patch.dict(sys.modules, {"psycopg": SimpleNamespace(connect=lambda *a, **kw: db)}):
        assert mirror_market_postgres("postgresql://example", market()) == 2
    assert [row[-1] for row in db.cur.rows] == [0, 42]
    assert "CASE WHEN EXCLUDED.spread_points>0" in db.cur.sql


def test_postgres_failure_does_not_change_successful_sqlite_result():
    m = market()
    with patch.dict("os.environ", {"RAMON_PG_MARKET_SHADOW_DSN": "postgresql://example"}):
        with patch("ramon.server.persist_market", return_value=2) as sqlite_write:
            with patch("ramon.server.mirror_market_postgres", side_effect=ConnectionError("unavailable")):
                assert persist_market_safely("ignored", m) == ""
    sqlite_write.assert_called_once()


def test_sqlite_failure_skips_shadow_write():
    m = market()
    with patch.dict("os.environ", {"RAMON_PG_MARKET_SHADOW_DSN": "postgresql://example"}):
        with patch("ramon.server.persist_market", side_effect=OSError("sqlite unavailable")):
            with patch("ramon.server.mirror_market_postgres") as pg_write:
                assert "OSError" in persist_market_safely("ignored", m)
    pg_write.assert_not_called()


def test_postgres_shadow_disabled_by_default():
    m = market()
    with patch.dict("os.environ", {}, clear=True):
        with patch("ramon.server.persist_market", return_value=2):
            with patch("ramon.server.mirror_market_postgres") as pg_write:
                assert persist_market_safely("ignored", m) == ""
    pg_write.assert_not_called()
