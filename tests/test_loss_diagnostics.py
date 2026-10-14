"""Offline behavior tests for descriptive loss diagnostics (no Docker/DB needed)."""
from ramon.loss_diagnostics import aggregate, loss_report


def test_grouping_keeps_profit_loss_separate():
    rows = [
        {"hour_tehran": "09:00", "direction": "BUY", "ea_version": "0.60.0",
         "exit_reason": "SL", "market_regime": "RANGE", "entry_source": "AUTO_RAMON",
         "net_units": -5.0},
        {"hour_tehran": "09:00", "direction": "SELL", "ea_version": "0.60.0",
         "exit_reason": "TP", "market_regime": "RANGE", "entry_source": "DASHBOARD_OPPORTUNITY",
         "net_units": 2.0},
    ]
    result = aggregate(rows)
    hour = result["hour_tehran"][0]
    assert hour["trades"] == 2
    assert hour["gross_profit_units"] == 2.0
    assert hour["gross_loss_units"] == 5.0
    assert hour["net_units"] == -3.0
    assert hour["profit_factor"] == 0.4
    assert len(result["entry_source"]) == 2


def test_nonpostgres_is_unavailable(monkeypatch):
    monkeypatch.setenv("RAMON_STORAGE_BACKEND", "sqlite")
    assert loss_report()["available"] is False
