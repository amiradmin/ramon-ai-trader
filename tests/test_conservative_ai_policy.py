"""Regression tests for opt-in conservative decision gates."""
from types import SimpleNamespace
from ramon.ai_decision_engine import apply_ai_decision_engine


def evaluate(**overrides):
    response = {
        "decision": "WAIT",
        "intrabar_confirmed": 0,
        "intrabar_direction": "BUY",
        "ai_trend_confirmed": 0,
        "ai_trend_direction": "BUY",
        "entry_timing_ready": 0,
        "market_direction": "NEUTRAL",
        "buy_success_probability": 0.70,
        "sell_success_probability": 0.40,
        "entry_probability": 0.62,
        "full_sl_probability": 0.15,
        "meta_probability": -1,
    }
    response.update(overrides)
    decision = SimpleNamespace(buy_edge=-0.25, sell_edge=-0.59)
    assert apply_ai_decision_engine(
        response, decision,
        {"route": "CONFIRMED_MODEL", "allowed_directions": ["BUY", "SELL"]},
        enabled=True, conservative_policy=True,
    )
    return response


def test_five_october_8_losses_blocked_for_unconfirmed_wait():
    # All five recorded decisions had base WAIT, with no confirmed
    # intrabar or AI trend vote and no ready entry timing.
    for chosen in ("BUY", "SELL", "BUY", "SELL", "BUY"):
        r = evaluate(
            buy_success_probability=0.72 if chosen == "BUY" else 0.45,
            sell_success_probability=0.75 if chosen == "SELL" else 0.40,
        )
        assert r["decision"] == "WAIT"
        assert r["ai_engine_v2_block"] == "unconfirmed_base_wait"


def test_ready_timing_still_required_even_after_confirmed_wait_recovery():
    r = evaluate(intrabar_confirmed=1, ai_trend_confirmed=1)
    assert r["decision"] == "WAIT"
    assert r["ai_engine_v2_block"] == "entry_timing_not_ready"


def test_conflicting_confirmed_vote_vetoes_candidate():
    r = evaluate(intrabar_confirmed=1, ai_trend_confirmed=1,
                 entry_timing_ready=1, ai_trend_direction="SELL")
    assert r["decision"] == "WAIT"
    assert r["ai_engine_v2_block"] == "ai_trend_direction_conflict"


def test_confirmed_direction_and_timing_allows_high_quality_candidate():
    r = evaluate(intrabar_confirmed=1, ai_trend_confirmed=1,
                 entry_timing_ready=1)
    assert r["decision"] == "BUY"
    assert r["ai_engine_v2_selected"] == 1
