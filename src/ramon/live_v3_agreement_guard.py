"""Conservative optional LIVE V3 consensus veto for autonomous V2 entries.

If enabled, may only change a V2 BUY/SELL to WAIT. Never authorizes trades.
Manual overrides, range and reversal execution paths are untouched.
"""
from __future__ import annotations


def apply_live_v3_agreement_guard(response: dict, *, enabled: bool = False) -> dict:
    initial = str(response.get("decision", "WAIT"))
    payload = {
        "v3_agreement_guard_enabled": int(enabled),
        "v3_agreement_guard_applied": 0,
        "v3_agreement_guard_reason": "disabled" if not enabled else "not_applicable",
        "v3_agreement_guard_initial_decision": initial,
    }
    if not enabled:
        response.update(payload)
        return payload
    # Do not interfere with any alternate strategies or human overrides.
    if (initial not in {"BUY", "SELL"}
        or not bool(response.get("ai_engine_v2_active"))
        or bool(response.get("range_execution"))
        or bool(response.get("reversal_execution"))
        or bool(response.get("manual_overrides_active"))
        or bool(response.get("manual_execution_override"))):
        response.update(payload)
        return payload
    v3_active = int(response.get("ai_engine_v3_shadow_active", 0) or 0) == 1
    candidate = str(response.get("ai_engine_v3_shadow_decision", "WAIT"))
    if not v3_active:
        reason = "v3_unavailable"
    elif candidate not in {"BUY", "SELL"}:
        payload["v3_agreement_guard_reason"] = "v3_wait_no_veto"
        response.update(payload)
        return payload
    elif candidate != initial:
        reason = "v3_disagreement"
    else:
        payload["v3_agreement_guard_reason"] = "agreed"
        response.update(payload)
        return payload
    response["decision"] = "WAIT"
    response["reason"] = "v3_agreement_" + reason
    response["range_execution"] = 0
    response["reversal_execution"] = 0
    payload["v3_agreement_guard_applied"] = 1
    payload["v3_agreement_guard_reason"] = reason
    response.update(payload)
    return payload
