from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from ramon.server import persist_market_safely


def test_pg_first_and_sqlite_always_kept():
    market = SimpleNamespace(timeframe="M15")
    env = {"RAMON_M15_WRITE_BACKEND": "postgres",
           "RAMON_PG_HISTORY_DSN": "dummy"}
    with patch.dict("os.environ", env, clear=True):
        with patch("ramon.server.mirror_market_postgres") as pg:
            with patch("ramon.server.persist_market") as sq:
                assert persist_market_safely("db", market) == ""
    pg.assert_called_once_with("dummy", market)
    sq.assert_called_once_with("db", market)


def test_pg_failure_falls_back_to_sqlite():
    market = SimpleNamespace(timeframe="M15")
    env = {"RAMON_M15_WRITE_BACKEND": "postgres",
           "RAMON_PG_HISTORY_DSN": "secret"}
    with patch.dict("os.environ", env, clear=True):
        with patch("ramon.server.mirror_market_postgres",
                   side_effect=OSError("secret")):
            with patch("ramon.server.persist_market") as sq:
                with patch("ramon.server.SHADOW_LOG.warning") as logger:
                    assert persist_market_safely("db", market) == ""
    sq.assert_called_once_with("db", market)
    assert all("secret" not in str(item.args[0]) for item in logger.call_args_list)
