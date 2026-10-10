"""Offline interleaving evidence, not a terminal mutex or broker acceptance test."""
from dataclasses import replace

from ramon.v2_account_risk import AccountSnapshot, Exposure, RiskPolicy, check_entry


POLICY = RiskPolicy(35.0, 35.0, 35.0, max_positions=10)
ACCOUNT = AccountSnapshot(True, True, True, False, 400.0, 1000.0, True)


def candidate(key, side="SELL", symbol="XAUUSD_l"):
    # Cent account: 20 account units = USD 0.20 at MoneyUnitsPerUSD=100.
    return Exposure(key, symbol, side, 20.0, 0.01)


def test_two_independent_preflights_can_both_pass_the_same_empty_snapshot():
    first, second = candidate("chart-a"), candidate("chart-b")
    a = check_entry(first, (), POLICY, ACCOUNT)
    b = check_entry(second, (), POLICY, ACCOUNT)
    assert a["allowed"] and b["allowed"]
    assert not a["live_order_access"] and not b["live_order_access"]
    assert first.risk_units + second.risk_units == 40.0 > POLICY.max_portfolio_risk_units


def test_serial_recheck_blocks_second_after_first_becomes_visible():
    first, second = candidate("chart-a"), candidate("chart-b")
    assert check_entry(first, (), POLICY, ACCOUNT)["allowed"]
    result = check_entry(second, (first,), POLICY, ACCOUNT)
    assert not result["allowed"]
    assert "portfolio_risk_limit" in result["reasons"]


def test_inflight_reservation_must_count_even_before_broker_position_is_visible():
    # Proposed coordination contract: a durable unresolved intent remains exposure.
    # No coordinator is implemented here; check_entry receives this reservation
    # explicitly, demonstrating the risk accounting it would have to enforce.
    inflight = candidate("unresolved-intent")
    other_symbol = candidate("other-chart", side="BUY", symbol="EURUSD")
    result = check_entry(other_symbol, (inflight,), POLICY, ACCOUNT)
    assert not result["allowed"]
    assert result["account_risk_units"] == 40.0
    assert result["reasons"] == ["portfolio_risk_limit"]


def test_unknown_inflight_stop_cannot_be_replaced_with_zero_risk():
    unresolved = replace(candidate("uncertain-intent"), has_stop=False)
    result = check_entry(candidate("next"), (unresolved,), POLICY, ACCOUNT)
    assert not result["allowed"]
    assert result["reasons"] == ["invalid_exposure"]
