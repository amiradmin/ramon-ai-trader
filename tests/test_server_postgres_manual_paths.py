from __future__ import annotations

import pytest

from ramon import server


def _opinion() -> dict[str, object]:
    return {"symbol": "XAUUSD_l", "sample_key": "a" * 16,
            "signal_bar_time": 1_800_000_000, "opinion": "BUY",
            "confidence": 4, "snapshot": {"source": "test"}}


def test_manual_helpers_use_postgres_without_a_sqlite_path(monkeypatch):
    calls = []

    class Repository:
        def __init__(self, dsn):
            assert dsn == "postgresql://isolated-test"

        def persist_human_opinion(self, value):
            calls.append(("opinion", value))

        def persist_manual_override(self, value):
            calls.append(("override", value))

        def recent_human_opinions(self, symbol, limit):
            calls.append(("recent", symbol, limit))
            return [{"symbol": symbol, "opinion": "BUY"}]

    monkeypatch.setenv("RAMON_STORAGE_BACKEND", "postgres")
    monkeypatch.setenv("RAMON_PG_DSN", "postgresql://isolated-test")
    monkeypatch.setattr("ramon.storage.PostgresOperationalRepository", Repository)
    server._persist_human_opinion("", _opinion())
    server._persist_manual_override("", {"signal_bar_time": 1_800_000_000,
                                           "sample_key": "a" * 16, "stage": "edge"})
    assert server._recent_human_opinions("", "XAUUSD_l", 99) == [{"symbol": "XAUUSD_l", "opinion": "BUY"}]
    assert [call[0] for call in calls] == ["opinion", "override", "recent"]
    assert calls[-1][-1] == 99  # repository enforces legacy 1..50 clamp


def test_postgres_manual_audit_failure_is_not_hidden(monkeypatch):
    class BrokenRepository:
        def __init__(self, dsn):
            pass

        def persist_manual_override(self, value):
            raise ConnectionError("audit unavailable")

    monkeypatch.setenv("RAMON_STORAGE_BACKEND", "postgres")
    monkeypatch.setenv("RAMON_PG_DSN", "postgresql://isolated-test")
    monkeypatch.setattr("ramon.storage.PostgresOperationalRepository", BrokenRepository)
    with pytest.raises(ConnectionError, match="audit unavailable"):
        server._persist_manual_override("", {"signal_bar_time": 1,
                                               "sample_key": "a" * 16, "stage": "edge"})
