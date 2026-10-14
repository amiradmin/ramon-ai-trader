from ramon.live_v3_agreement_guard import apply_live_v3_agreement_guard


def response(**overrides):
    item = {
        "decision": "BUY",
        "ai_engine_v2_active": 1,
        "ai_engine_v3_shadow_active": 1,
        "ai_engine_v3_shadow_decision": "BUY",
        "range_execution": 0,
        "reversal_execution": 0,
    }
    item.update(overrides)
    return item


def test_default_observe_does_not_change_trade():
    item = response(ai_engine_v3_shadow_decision="SELL")
    apply_live_v3_agreement_guard(item)
    assert item["decision"] == "BUY"
    assert item["v3_agreement_guard_applied"] == 0


def test_agreement_keeps_live_entry():
    item = response()
    apply_live_v3_agreement_guard(item, enabled=True)
    assert item["decision"] == "BUY"
    assert item["v3_agreement_guard_reason"] == "agreed"


def test_disagreement_changes_entry_to_wait():
    item = response(ai_engine_v3_shadow_decision="SELL")
    apply_live_v3_agreement_guard(item, enabled=True)
    assert item["decision"] == "WAIT"
    assert item["reason"] == "v3_agreement_v3_disagreement"
    assert item["v3_agreement_guard_applied"] == 1


def test_missing_v3_fails_closed_for_v2_entry():
    item = response(ai_engine_v3_shadow_active=0)
    apply_live_v3_agreement_guard(item, enabled=True)
    assert item["decision"] == "WAIT"
    assert item["v3_agreement_guard_reason"] == "v3_unavailable"


def test_never_changes_wait_to_entry():
    item = response(decision="WAIT", ai_engine_v3_shadow_decision="BUY")
    apply_live_v3_agreement_guard(item, enabled=True)
    assert item["decision"] == "WAIT"
    assert item["v3_agreement_guard_applied"] == 0


def test_manual_range_reversal_not_affected():
    for extra in (
        {"range_execution": 1},
        {"reversal_execution": 1},
        {"manual_execution_override": 1},
        {"manual_overrides_active": ["decision"]},
    ):
        item = response(ai_engine_v3_shadow_decision="WAIT", **extra)
        apply_live_v3_agreement_guard(item, enabled=True)
        assert item["decision"] == "BUY"
        assert item["v3_agreement_guard_applied"] == 0


def test_cannot_resurrect_v2_inactive_entry():
    item = response(ai_engine_v2_active=0, ai_engine_v3_shadow_decision="WAIT")
    apply_live_v3_agreement_guard(item, enabled=True)
    assert item["decision"] == "BUY"
