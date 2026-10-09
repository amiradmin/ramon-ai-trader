from copy import deepcopy

from ramon.ai_decision_engine_v3 import apply_ai_engine_v3_shadow


def _response(**overrides):
    base={
        "decision":"BUY",
        "buy_success_probability":.70,
        "sell_success_probability":.55,
        "entry_timing_ready":1,
        "market_direction":"BUY",
        "moment_anomaly_label":"NORMAL",
    }
    base.update(overrides)
    return base


def test_v3_shadow_never_changes_live_decision():
    r=_response()
    before=deepcopy(r)
    out=apply_ai_engine_v3_shadow(
        r,{"state":"TREND_UP","route":"CONFIRMED_MODEL"},
        {"forecast_distance_atr":.4},
    )
    assert r["decision"]==before["decision"]
    assert out["ai_engine_v3_shadow_decision"]=="BUY"
    assert out["ai_engine_v3_shadow_authoritative"]==0


def test_v3_shadow_preserves_base_wait():
    r=_response(decision="WAIT")
    out=apply_ai_engine_v3_shadow(
        r,{"state":"TREND_UP","route":"CONFIRMED_MODEL"},
        {"forecast_distance_atr":.4},
    )
    assert r["decision"]=="WAIT"
    assert out["ai_engine_v3_shadow_decision"]=="WAIT"
    assert out["ai_engine_v3_shadow_block"]=="base_wait_preserved"


def test_v3_shadow_separates_timing_from_direction():
    r=_response(entry_timing_ready=0)
    out=apply_ai_engine_v3_shadow(
        r,{"state":"TREND_UP","route":"CONFIRMED_MODEL"},
        {"forecast_distance_atr":.4},
    )
    assert out["ai_engine_v3_shadow_candidate_direction"]=="BUY"
    assert out["ai_engine_v3_shadow_decision"]=="WAIT"
    assert out["ai_engine_v3_shadow_block"]=="entry_timing_not_ready"


def test_v3_shadow_blocks_extension_and_bad_state():
    r=_response()
    out=apply_ai_engine_v3_shadow(
        r,{"state":"RANGE_MIDDLE","route":"CONFIRMED_MODEL"},
        {"forecast_distance_atr":1.2},
    )
    assert out["ai_engine_v3_shadow_decision"]=="WAIT"
    assert out["ai_engine_v3_shadow_block"]=="market_state_gate"
