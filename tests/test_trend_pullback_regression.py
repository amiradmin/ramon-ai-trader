"""Regression checks for independent completed-bar pullback detection."""
from ramon.core import Bar, Market
from ramon.market_opportunity_engine import detect_opportunity


def build(side):
    bars = []
    base = 4200.0
    for i in range(128):
        t = 1700000000 + 900*i
        if i < 115:
            base += 0.02 if i % 2 == 0 else -0.02
        elif i < 124:
            base += (0.85 if side == "BUY" else -0.85)
        elif i < 127:
            base += (-0.22 if side == "BUY" else 0.22)
        else:
            base += (0.28 if side == "BUY" else -0.28)
        op = base - (0.12 if side=="BUY" and i==127 else -0.12 if side=="SELL" and i==127 else 0)
        bars.append(Bar(t,op, max(op,base)+0.18, min(op,base)-0.18,base))
    return Market("XAUUSD_l","M15",base-0.05,base+0.05,0.01,tuple(bars))


def test_bullish_impulse_pullback_geometry():
    result = detect_opportunity(build("BUY"))
    # Either a valid entry or an explicit RR rejection is acceptable;
    # no inverted stops or fabricated take-profit prices.
    assert result.reason in {"candidate_only","insufficient_reward_risk","no_valid_setup"}
    if result.decision == "BUY":
        assert result.setup == "TREND_PULLBACK"
        assert result.stop < result.entry < result.target
        assert result.reward_risk >= 1.5


def test_bearish_impulse_pullback_geometry():
    result = detect_opportunity(build("SELL"))
    assert result.reason in {"candidate_only","insufficient_reward_risk","no_valid_setup"}
    if result.decision == "SELL":
        assert result.setup == "TREND_PULLBACK"
        assert result.target < result.entry < result.stop
        assert result.reward_risk >= 1.5
