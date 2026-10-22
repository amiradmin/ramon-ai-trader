"""Ramon AI Engine V3 shadow policy.

V3 separates direction selection from execution timing. It is observe-only:
this module MUST NOT mutate response["decision"], execution geometry, or live
authorization. It only annotates shadow fields for audit and validation.
"""
from __future__ import annotations

from math import isfinite


def _prob(value, default=-1.0):
    try:
        x=float(value)
    except (TypeError,ValueError):
        return default
    return x if isfinite(x) and 0.0 <= x <= 1.0 else default


def _float(value, default=None):
    try:
        x=float(value)
    except (TypeError,ValueError):
        return default
    return x if isfinite(x) else default


def apply_ai_engine_v3_shadow(
    response: dict,
    assessment: dict,
    entry_features: dict | None = None,
    *,
    enabled: bool = True,
    minimum_direction_quality: float = 0.55,
    minimum_quality_margin: float = 0.05,
    maximum_forecast_distance_atr: float = 0.80,
    require_entry_timing: bool = True,
    preserve_base_wait: bool = True,
    block_moment_elevated: bool = True,
) -> dict:
    """Compute a non-authoritative V3 recommendation and attach audit fields."""
    original_decision=response.get("decision")
    buy=_prob(response.get("buy_success_probability"))
    sell=_prob(response.get("sell_success_probability"))
    timing_ready=bool(response.get("entry_timing_ready"))
    market_direction=str(response.get("market_direction","NEUTRAL"))
    state=str(assessment.get("state","UNKNOWN"))
    route=str(assessment.get("route",""))
    moment_label=str(response.get("moment_anomaly_label","UNKNOWN"))
    forecast_distance=_float((entry_features or {}).get("forecast_distance_atr"))

    rec="WAIT"
    reason="disabled"
    candidate="NONE"
    block="disabled"
    quality=-1.0
    margin=-1.0

    if enabled:
        if buy >= 0 and sell >= 0:
            candidate="BUY" if buy > sell else "SELL"
            quality=max(buy,sell)
            margin=abs(buy-sell)
        if route=="WAIT":
            reason=block="market_hazard"
        elif preserve_base_wait and str(original_decision)=="WAIT":
            reason=block="base_wait_preserved"
        elif buy < 0 or sell < 0:
            reason=block="direction_quality_unavailable"
        elif quality < minimum_direction_quality:
            reason=block="direction_quality_low"
        elif margin < minimum_quality_margin:
            reason=block="direction_ambiguous"
        elif market_direction in {"BUY","SELL"} and market_direction != candidate:
            reason=block="market_direction_conflict"
        elif state in {"RANGE_MIDDLE","VOLATILITY_SHOCK"}:
            reason=block="market_state_gate"
        elif block_moment_elevated and moment_label=="ELEVATED":
            reason=block="moment_elevated"
        elif require_entry_timing and not timing_ready:
            reason=block="entry_timing_not_ready"
        elif forecast_distance is not None and forecast_distance > maximum_forecast_distance_atr:
            reason=block="forecast_extension"
        else:
            rec=candidate
            reason="shadow_"+candidate.lower()
            block=""

    payload={
        "ai_engine_v3_shadow_enabled":int(enabled),
        "ai_engine_v3_shadow_active":int(enabled),
        "ai_engine_v3_shadow_decision":rec,
        "ai_engine_v3_shadow_reason":reason,
        "ai_engine_v3_shadow_block":block,
        "ai_engine_v3_shadow_candidate_direction":candidate,
        "ai_engine_v3_shadow_buy_quality":buy,
        "ai_engine_v3_shadow_sell_quality":sell,
        "ai_engine_v3_shadow_direction_quality":quality,
        "ai_engine_v3_shadow_quality_margin":margin,
        "ai_engine_v3_shadow_entry_timing_ready":int(timing_ready),
        "ai_engine_v3_shadow_forecast_distance_atr":forecast_distance,
        "ai_engine_v3_shadow_max_forecast_distance_atr":maximum_forecast_distance_atr,
        "ai_engine_v3_shadow_market_state":state,
        "ai_engine_v3_shadow_market_direction":market_direction,
        "ai_engine_v3_shadow_moment_label":moment_label,
        "ai_engine_v3_shadow_original_decision":original_decision,
        "ai_engine_v3_shadow_authoritative":0,
    }
    response.update(payload)
    # Safety invariant: shadow V3 may annotate only.
    assert response.get("decision")==original_decision
    return payload
