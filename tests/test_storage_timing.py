from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from ramon.server import persist_market_safely


def _market():
    return SimpleNamespace(timeframe="M15")


def test_timing_disabled_by_default():
    with patch.dict("os.environ", {}, clear=True):
        with patch("ramon.server.persist_market", return_value=1):
            with patch("ramon.server.SHADOW_LOG.warning") as log:
                assert persist_market_safely("ignored", _market()) == ""
    log.assert_not_called()


def test_timing_reports_sqlite_and_shadow_independently():
    with patch.dict("os.environ", {"RAMON_STORAGE_TIMING_ENABLED": "1",
                                   "RAMON_PG_MARKET_SHADOW_DSN": "fake"}):
        with patch("ramon.server.persist_market", return_value=1):
            with patch("ramon.server.mirror_market_postgres", return_value=1):
                with patch("ramon.server.SHADOW_LOG.warning") as log:
                    assert persist_market_safely("ignored", _market()) == ""
    messages = [str(call.args[0]) for call in log.call_args_list]
    assert any("stage=sqlite status=ok" in msg for msg in messages)
    assert any("stage=postgres_shadow status=ok" in msg for msg in messages)
    assert all("fake" not in msg for msg in messages)


def test_timing_reports_shadow_error_without_failing_request():
    with patch.dict("os.environ", {"RAMON_STORAGE_TIMING_ENABLED": "1",
                                   "RAMON_PG_MARKET_SHADOW_DSN": "secret"}):
        with patch("ramon.server.persist_market", return_value=1):
            with patch("ramon.server.mirror_market_postgres", side_effect=OSError("secret")):
                with patch("ramon.server.SHADOW_LOG.warning") as log:
                    assert persist_market_safely("ignored", _market()) == ""
    messages = [str(call.args[0]) for call in log.call_args_list]
    assert any("stage=postgres_shadow status=error" in msg for msg in messages)
    assert all("secret" not in msg for msg in messages)
