"""Safety gates against the Oct 8 overlapping SELL / stop-out case."""
from dataclasses import replace
import pytest
from ramon.v2_account_risk import Exposure, RiskPolicy, AccountSnapshot, check_entry


def exposure(key, risk=100, side="SELL", symbol="XAUUSD_l"):
    return Exposure(key, symbol, side, risk, .1)


def account(**changes):
    base = AccountSnapshot(
        all_positions_included=True,
        pending_orders_included=True,
        mt5_connected=True,
        stopout_lock=False,
        expected_post_trade_margin_level_pct=400,
        expected_post_trade_free_margin_units=1000,
        margin_check_success=True,
    )
    return replace(base, **changes)


def policy():
    return RiskPolicy(max_portfolio_risk_units=300,
                      max_symbol_risk_units=300,
                      max_side_risk_units=300,
                      max_positions=3)


def test_oct08_two_sells_would_be_blocked():
    # Each individual trade was approximately 290 units of risk,
    # but opening two together implied around 580 units exposure.
    result = check_entry(exposure("new", 292.50),
                         [exposure("existing", 287.82)], policy(), account())
    assert not result["allowed"]
    assert result["account_risk_units"] == 580.32
    assert "portfolio_risk_limit" in result["reasons"]
    assert "direction_risk_limit" in result["reasons"]


def test_account_wide_exposure_includes_other_symbols_and_versions():
    existing = [exposure("manual-eurusd", 250, symbol="EURUSD")]
    result = check_entry(exposure("ramon", 100), existing, policy(), account())
    assert "portfolio_risk_limit" in result["reasons"]
    assert "symbol_risk_limit" not in result["reasons"]


def test_stopout_latches_and_missing_margins_fail_closed():
    result = check_entry(exposure("new"), (), policy(), account(stopout_lock=True))
    assert "stopout_lock" in result["reasons"]
    result = check_entry(exposure("new"), (), policy(), account(
        margin_check_success=False, expected_post_trade_margin_level_pct=None))
    assert "margin_check_failed" in result["reasons"]
    assert "margin_level_below_floor" in result["reasons"]


@pytest.mark.parametrize("updates,reason", [
    ({"mt5_connected": False}, "incomplete_account_snapshot"),
    ({"all_positions_included": False}, "incomplete_account_snapshot"),
    ({"pending_orders_included": False}, "incomplete_account_snapshot"),
    ({"snapshot_age_seconds": 6}, "stale_account_snapshot"),
    ({"expected_post_trade_margin_level_pct": 200}, "margin_level_below_floor"),
])
def test_missing_critical_data_blocks_entry(updates, reason):
    result = check_entry(exposure("new"), (), policy(), account(**updates))
    assert not result["allowed"]
    assert reason in result["reasons"]


def test_normal_entry_and_position_limit():
    assert check_entry(exposure("new", 50), (), policy(), account())["allowed"]
    p = replace(policy(), max_positions=1)
    assert "position_count_limit" in check_entry(exposure("new"), [
        exposure("existing", 10)], p, account())["reasons"]


def test_invalid_or_unprotected_exposure_never_passes():
    result = check_entry(replace(exposure("new"), has_stop=False),
                         (), policy(), account())
    assert result["reasons"] == ["invalid_exposure"]
    result = check_entry(exposure("new"), [
        replace(exposure("other"), risk_units=float("nan"))],
        policy(), account())
    assert result["reasons"] == ["invalid_exposure"]
