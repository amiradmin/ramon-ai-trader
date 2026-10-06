from types import SimpleNamespace

from ramon.trade_selector import apply_live_selector


def decision(**overrides):
    values = dict(
        reason="insufficient_model_edge",
        buy_edge=0.30,
        sell_edge=-0.20,
        trend_edge_floor=0.20,
        signal_strength=0.10,
        intrabar_min_strength=0.05,
        ai_trend_confirmed=1,
        ai_trend_direction="BUY",
        intrabar_turn_confirmed=1,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def response(**overrides):
    values = {
        "decision": "WAIT",
        "shadow_buy_success_probability": 0.68,
        "shadow_sell_success_probability": 0.42,
        "shadow_full_sl_probability": 0.31,
    }
    values.update(overrides)
    return values


def assessment(**overrides):
    values = {
        "route": "CONFIRMED_MODEL",
        "allowed_directions": ["BUY"],
    }
    values.update(overrides)
    return values


def apply(r, d=None, a=None):
    return apply_live_selector(
        r,
        d or decision(),
        a or assessment(),
        enabled=True,
        minimum_quality=0.55,
        maximum_full_sl_probability=0.50,
        minimum_quality_margin=0.05,
    )


def test_selector_recovers_confirmed_low_edge_buy():
    r = response()
    assert apply(r)
    assert r["decision"] == "BUY"
    assert r["reason"] == "selector_v2_buy"
    assert r["selector_live_selected"] == 1


def test_selector_never_bypasses_market_hazard_route():
    r = response()
    assert not apply(r, a=assessment(route="WAIT", allowed_directions=[]))
    assert r["decision"] == "WAIT"


def test_selector_requires_quality_margin_and_bounded_full_sl_risk():
    r = response(shadow_buy_success_probability=0.56, shadow_sell_success_probability=0.54)
    assert not apply(r)
    r = response(shadow_full_sl_probability=0.70)
    assert not apply(r)


def test_selector_does_not_rescue_other_wait_reasons():
    r = response()
    assert not apply(r, d=decision(reason="trend_conflict"))
    assert r["decision"] == "WAIT"


def test_selector_requires_chronos_path_confirmation_and_edge_floor():
    r = response()
    assert not apply(r, d=decision(ai_trend_confirmed=0))
    r = response()
    assert not apply(r, d=decision(buy_edge=0.10, trend_edge_floor=0.20))
