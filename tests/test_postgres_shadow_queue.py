from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from ramon.postgres_shadow_queue import ShadowQueue
from ramon.server import persist_market_safely


def test_full_queue_never_blocks_request():
    queue = ShadowQueue(capacity=1, writer=lambda *args: None)
    with patch.object(queue._queue, "put_nowait", side_effect=__import__("queue").Full):
        assert queue.submit("secret-dsn", SimpleNamespace()) is False


def test_async_flag_queues_only_after_successful_sqlite_write():
    market = SimpleNamespace(timeframe="M15")
    env = {"RAMON_PG_MARKET_SHADOW_DSN": "fake", "RAMON_PG_SHADOW_ASYNC_ENABLED": "1"}
    with patch.dict("os.environ", env):
        with patch("ramon.server.persist_market", return_value=1):
            with patch("ramon.server.submit_shadow", return_value=True) as submit:
                with patch("ramon.server.mirror_market_postgres") as direct:
                    assert persist_market_safely("ignored", market) == ""
    submit.assert_called_once_with("fake", market)
    direct.assert_not_called()


def test_sqlite_failure_never_queues():
    market = SimpleNamespace(timeframe="M15")
    env = {"RAMON_PG_MARKET_SHADOW_DSN": "fake", "RAMON_PG_SHADOW_ASYNC_ENABLED": "1"}
    with patch.dict("os.environ", env):
        with patch("ramon.server.persist_market", side_effect=OSError("sqlite")):
            with patch("ramon.server.submit_shadow") as submit:
                assert "OSError" in persist_market_safely("ignored", market)
    submit.assert_not_called()


def test_shadow_disabled_by_default():
    market = SimpleNamespace(timeframe="M15")
    with patch.dict("os.environ", {}, clear=True):
        with patch("ramon.server.persist_market", return_value=1):
            with patch("ramon.server.submit_shadow") as submit:
                assert persist_market_safely("ignored", market) == ""
    submit.assert_not_called()
