from __future__ import annotations

import io

import pytest

import ramon.audit as audit_module
from ramon.sizing_audit import group_stats, match_override_candidates


def test_live_health_retries_transient_startup_failure(monkeypatch):
    calls = []

    def fake_urlopen(url, timeout):
        calls.append((url, timeout))
        if len(calls) == 1:
            raise OSError("connection refused")
        return io.BytesIO(b'{"ready":true,"model":"test/model"}')

    monkeypatch.setattr(audit_module, "urlopen", fake_urlopen)
    monkeypatch.setattr(audit_module.time, "sleep", lambda _: None)

    health = audit_module.read_live_health(
        "http://127.0.0.1:8012/health",
        attempts=3,
        delay_seconds=0.01,
        timeout_seconds=2,
    )

    assert health == {"ready": True, "model": "test/model"}
    assert len(calls) == 2


def test_live_health_reports_failure_only_after_all_attempts(monkeypatch):
    monkeypatch.setattr(
        audit_module,
        "urlopen",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("connection refused")),
    )
    monkeypatch.setattr(audit_module.time, "sleep", lambda _: None)

    health = audit_module.read_live_health("http://127.0.0.1:8012/health", attempts=3)

    assert health["status"] == "UNAVAILABLE"
    assert health["attempts"] == 3
    assert "connection refused" in health["error"]


def test_override_stats_use_only_unambiguous_strict_candidates():
    trades = [
        {
            "trade_key": "t1",
            "direction": "BUY",
            "quote_time": 100,
            "initial_risk_units": 18.0,
            "net_units": -18.0,
            "net_r": -1.0,
        },
        {
            "trade_key": "t2",
            "direction": "BUY",
            "quote_time": 200,
            "initial_risk_units": 6.0,
            "net_units": 12.0,
            "net_r": 2.0,
        },
        {
            "trade_key": "t3",
            "direction": "SELL",
            "quote_time": 300,
            "initial_risk_units": 6.0,
            "net_units": -6.0,
            "net_r": -1.0,
        },
    ]
    signals = [
        {
            "_stamp": 101,
            "decision": "BUY",
            "min_lot_override_used": "YES",
            "risk_budget_units": "6.0",
            "min_lot_sl_units": "18.0",
            "planned_volume": "0.01",
        },
        {
            "_stamp": 201,
            "decision": "BUY",
            "min_lot_override_used": "NO",
            "risk_budget_units": "6.0",
            "min_lot_sl_units": "5.0",
            "planned_volume": "0.01",
        },
        # Equal-distance contradictory candidates must be excluded as ambiguous.
        {
            "_stamp": 299,
            "decision": "SELL",
            "min_lot_override_used": "YES",
            "risk_budget_units": "6.0",
            "min_lot_sl_units": "9.0",
            "planned_volume": "0.01",
        },
        {
            "_stamp": 301,
            "decision": "SELL",
            "min_lot_override_used": "NO",
            "risk_budget_units": "6.0",
            "min_lot_sl_units": "6.0",
            "planned_volume": "0.01",
        },
    ]

    matches, coverage = match_override_candidates(trades, signals, window_seconds=5)
    stats = group_stats(matches)

    assert coverage == {
        "closed_trades": 3,
        "quote_time_available": 3,
        "matched": 2,
        "ambiguous": 1,
        "unmatched": 0,
    }
    assert stats["YES"]["trades"] == 1
    assert stats["YES"]["net_units"] == pytest.approx(-18.0)
    assert stats["YES"]["avg_risk_to_budget"] == pytest.approx(3.0)
    assert stats["NO"]["trades"] == 1
    assert stats["NO"]["net_units"] == pytest.approx(12.0)
    assert stats["NO"]["avg_risk_to_budget"] == pytest.approx(1.0)
