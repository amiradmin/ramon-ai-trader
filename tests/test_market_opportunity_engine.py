from ramon.core import Bar, Market
from ramon.market_opportunity_engine import detect_opportunity


def market(closes=None, spread=0.42):
    closes = closes or [4000.0 + i * .12 for i in range(140)]
    bars = tuple(Bar(1_700_000_000 + 900*i, v-.15, v+.5, v-.5, v) for i,v in enumerate(closes))
    mid = closes[-1]
    return Market("XAUUSD_l", "M15", mid-spread/2, mid+spread/2, .01, bars)


def test_wide_spread_veto():
    assert detect_opportunity(market(spread=20)).reason == "spread_too_wide"


def test_deterministic_and_never_invents_trade():
    first = detect_opportunity(market())
    assert first == detect_opportunity(market())
    assert first.decision in {"BUY", "SELL", "WAIT"}


def test_geometry_if_candidate():
    item = detect_opportunity(market())
    if item.decision == "BUY":
        assert item.stop < item.entry < item.target
        assert item.reward_risk >= 1.5
    if item.decision == "SELL":
        assert item.target < item.entry < item.stop
        assert item.reward_risk >= 1.5


def test_invalid_market_is_rejected():
    from dataclasses import replace
    import pytest
    broken = replace(market(), ask=0.0)
    with pytest.raises(ValueError):
        detect_opportunity(broken)


def test_optional_confirmation_never_adds_unconfirmed_candidate():
    baseline = detect_opportunity(market())
    confirmed = detect_opportunity(market(), require_reversal_confirmation=True)
    if baseline.decision == "WAIT":
        assert confirmed.decision == "WAIT"
    if confirmed.decision != "WAIT":
        assert confirmed.decision == baseline.decision
        assert confirmed.setup == baseline.setup
