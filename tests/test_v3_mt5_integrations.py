import sqlite3

import pytest

from ramon.v3_mt5_integrations import (
    FEATURES, capability_report, create_event_store, store_trade_transaction,
    normalize_calendar_event, replay_asof, optimize_shadow, safe_mcp_config,
)


def test_eight_features_do_not_claim_live_access():
    report = capability_report()
    assert set(report["features"]) == set(FEATURES)
    assert report["mode"] == "OBSERVE_ONLY"
    assert report["live_order_access"] is False


def test_mcp_config_must_be_local_read_only():
    assert safe_mcp_config({"endpoint": "http://127.0.0.1:5555",
                            "allowed_tools": ["get_account", "list_orders"]})
    assert not safe_mcp_config({"endpoint": "http://0.0.0.0:5555",
                                "allowed_tools": ["get_account"]})
    assert not safe_mcp_config({"endpoint": "http://127.0.0.1:5555",
                                "allowed_tools": ["order_send"]})


def test_transaction_store_is_idempotent(tmp_path):
    db = create_event_store(tmp_path / "events.sqlite3")
    event = {"event_key": "deal:1:sl:2", "event_kind": "MANUAL_SL_CHANGE",
             "position_id": "123", "timestamp_utc": 1234, "sl": 4200.0}
    assert store_trade_transaction(db, event)
    assert not store_trade_transaction(db, event)
    assert db.execute("SELECT count(*) FROM mt5_trade_events").fetchone()[0] == 1
    with pytest.raises(ValueError):
        store_trade_transaction(db, {**event, "timestamp_utc": True})
    db.close()


def test_no_future_leakage():
    decisions = [{"published_at_utc": 150, "signal": "BUY"},
                 {"published_at_utc": 99, "signal": "SELL"}]
    assert replay_asof(decisions, at_utc=100, max_age_seconds=10)["signal"] == "SELL"
    assert replay_asof(decisions, at_utc=120, max_age_seconds=10) is None


def test_calendar_requires_utc_and_known_impact():
    assert normalize_calendar_event(
        {"timestamp_utc": 123, "currency": "usd", "impact": "HIGH"})["currency"] == "USD"
    with pytest.raises(ValueError):
        normalize_calendar_event({"timestamp_utc": "2026-10-10 10:00",
                                  "currency": "USD", "impact": "HIGH"})


def test_shadow_optimization_never_submits_trades():
    best = optimize_shadow([1, 2, 3], {"threshold": [1, 2, 3]},
                           lambda samples, params: -abs(params["threshold"] - 2))
    assert best == {"params": {"threshold": 2}, "score": 0.0}
