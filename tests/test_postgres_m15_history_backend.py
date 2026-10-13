from __future__ import annotations

import sqlite3
from unittest.mock import patch

import pytest

from ramon.history import load_bars


def _sqlite(path):
    with sqlite3.connect(path) as c:
        c.execute("""CREATE TABLE history_bars(
            symbol TEXT,timeframe TEXT,time INTEGER,open REAL,high REAL,
            low REAL,close REAL,spread_points INTEGER)""")
        c.execute("INSERT INTO history_bars VALUES(?,?,?,?,?,?,?,?)",
                  ("XAUUSD_l","M15",900,100,103,99,102,42))


def test_default_remains_sqlite(tmp_path):
    db = tmp_path / "test.sqlite3"
    _sqlite(db)
    with patch.dict("os.environ", {}, clear=True):
        bars, spreads = load_bars(db)
    assert bars[0].close == 102
    assert spreads == (42,)


def test_pg_selected_no_implicit_fallback(tmp_path):
    db = tmp_path / "test.sqlite3"
    _sqlite(db)
    with patch.dict("os.environ", {"RAMON_M15_HISTORY_BACKEND":"postgres",
                                   "RAMON_PG_HISTORY_DSN":"dummy"}, clear=True):
        with patch("ramon.postgres_m15_history.load_m15_postgres",
                   side_effect=OSError("unavailable")):
            with pytest.raises(OSError):
                load_bars(db)


def test_pg_selected_explicit_fallback(tmp_path):
    db = tmp_path / "test.sqlite3"
    _sqlite(db)
    env = {"RAMON_M15_HISTORY_BACKEND":"postgres",
           "RAMON_M15_HISTORY_FALLBACK_SQLITE":"1",
           "RAMON_PG_HISTORY_DSN":"dummy"}
    with patch.dict("os.environ", env, clear=True):
        with patch("ramon.postgres_m15_history.load_m15_postgres",
                   side_effect=OSError("unavailable")):
            bars, spreads = load_bars(db)
    assert bars[0].close == 102
    assert spreads == (42,)


def test_pg_selected_reads_pg_even_without_sqlite_file(tmp_path):
    with patch.dict("os.environ", {"RAMON_M15_HISTORY_BACKEND":"postgres",
                                   "RAMON_PG_HISTORY_DSN":"dummy"}, clear=True):
        with patch("ramon.postgres_m15_history.load_m15_postgres",
                   return_value=(("pg-bar",), (8,))):
            assert load_bars(tmp_path / "missing.sqlite3") == (("pg-bar",), (8,))
