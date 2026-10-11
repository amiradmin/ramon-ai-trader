from dataclasses import replace

from ramon.core import Bar, Market
from ramon.decision_safety_shadow import assess_data_quality, no_trade_recommendation


def market(*, bars=256, micro=True):
    values = tuple(Bar(1_800_000_000 + i * 900, 100, 101, 99, 100.5) for i in range(bars))
    quote = values[-1].time + 930
    m1 = tuple(Bar(quote - 120 + i * 60, 100, 101, 99, 100.5) for i in range(3)) if micro else ()
    return Market("XAUUSD_l", "M15", 100.0, 100.2, 0.01, values, m1), quote


def test_quality_is_observational_and_identifies_missing_context():
    item, quote = market(bars=128, micro=False)
    quality = assess_data_quality(item, quote_time=quote, max_spread_points=50)
    assert quality["data_quality_shadow_ready"] == 0
    assert {"short_context", "micro_context_missing"} <= set(quality["data_quality_shadow_reasons"])


def test_no_trade_shadow_never_changes_a_live_decision():
    item, quote = market()
    quality = assess_data_quality(item, quote_time=quote, max_spread_points=50)
    response = {"decision": "BUY", "buy_success_probability": .51, "sell_success_probability": .50}
    result = no_trade_recommendation(response, quality, {"route": "CONFIRMED_MODEL"})
    assert response["decision"] == "BUY"
    assert result["no_trade_shadow_recommendation"] == "WAIT"
    assert "direction_ambiguous" in result["no_trade_shadow_reasons"]
