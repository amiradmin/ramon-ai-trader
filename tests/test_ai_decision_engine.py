from types import SimpleNamespace

from ramon.ai_decision_engine import apply_ai_decision_engine


def decision(**overrides):
    values = dict(
        reason="insufficient_model_edge",
        buy_edge=0.30,
        sell_edge=-0.10,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def response(**overrides):
    values = {
        "decision": "WAIT",
        "shadow_buy_success_probability": 0.72,
        "shadow_sell_success_probability": 0.43,
        "entry_probability": 0.68,
        "regime_probability": 0.61,
        "news_probability": 0.57,
        "meta_probability": -1.0,
        "shadow_full_sl_probability": 0.28,
        "moment_anomaly_ratio": 1.4,
    }
    values.update(overrides)
    return values


def assessment(route="CONFIRMED_MODEL"):
    return {"route": route, "allowed_directions": ["BUY"]}


def apply(r, d=None, a=None):
    return apply_ai_decision_engine(
        r,
        d or decision(),
        a or assessment(),
        enabled=True,
        minimum_score=0.56,
        minimum_quality_margin=0.03,
        anomaly_soft_threshold=2.0,
        anomaly_hard_threshold=5.0,
    )


def test_engine_recovers_analytical_wait_from_specialist_consensus():
    r = response()
    assert apply(r)
    assert r["decision"] == "BUY"
    assert r["reason"] == "ai_engine_v2_buy"
    assert r["ai_engine_v2_selected"] == 1
    assert r["ai_engine_v2_score_source"] == "specialist_consensus"


def test_engine_vetoes_weak_learned_consensus():
    r = response(
        shadow_buy_success_probability=0.54,
        shadow_sell_success_probability=0.50,
        entry_probability=0.42,
        shadow_full_sl_probability=0.58,
        news_probability=0.45,
        regime_probability=0.45,
    )
    assert apply(r)
    assert r["decision"] == "WAIT"
    assert r["reason"] == "ai_engine_v2_veto"


def test_engine_never_bypasses_market_hazard_route():
    r = response()
    assert not apply(r, a=assessment("WAIT"))
    assert r["decision"] == "WAIT"
    assert r["ai_engine_v2_block"] == "market_hazard_route"


def test_engine_never_recovers_nonrecoverable_timing_hazard():
    r = response()
    assert not apply(r, d=decision(reason="adverse_intrabar_timing"))
    assert r["decision"] == "WAIT"


def test_anomaly_is_soft_until_extreme():
    r = response(moment_anomaly_ratio=3.5)
    assert apply(r)
    assert r["ai_engine_v2_anomaly_penalty"] > 0
    assert r["reason"] in {"ai_engine_v2_buy", "ai_engine_v2_veto"}

    r = response(moment_anomaly_ratio=5.2)
    assert apply(r)
    assert r["decision"] == "WAIT"
    assert r["reason"] == "ai_engine_v2_extreme_anomaly"


def test_learned_meta_becomes_primary_input_when_available():
    r = response(meta_probability=0.82)
    assert apply(r)
    assert r["ai_engine_v2_score_source"] == "learned_meta_plus_specialists"
