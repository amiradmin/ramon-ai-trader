"""Learned live selector for recovering high-quality low-edge entries.

This selector may create an entry only from the narrow
`insufficient_model_edge` state. Structural market hazards, external-model
vetoes, news/account/execution gates and the EA remain authoritative.
"""
from __future__ import annotations

from .core import Decision
from .ensemble import dominant_direction


def apply_live_selector(
    response: dict,
    decision: Decision,
    assessment: dict,
    *,
    enabled: bool,
    minimum_quality: float,
    maximum_full_sl_probability: float,
    minimum_quality_margin: float,
) -> bool:
    response.update({
        "selector_live_enabled": int(enabled),
        "selector_live_selected": 0,
        "selector_minimum_quality": minimum_quality,
        "selector_maximum_full_sl_probability": maximum_full_sl_probability,
        "selector_minimum_quality_margin": minimum_quality_margin,
    })
    if not enabled:
        return False
    if str(response.get("decision", "")) != "WAIT":
        return False
    if decision.reason != "insufficient_model_edge":
        return False

    side = dominant_direction(decision)
    if side not in {"BUY", "SELL"}:
        return False
    if assessment.get("route") != "CONFIRMED_MODEL":
        return False
    if side not in set(assessment.get("allowed_directions", ())):
        return False

    quality_key = (
        "shadow_buy_success_probability" if side == "BUY"
        else "shadow_sell_success_probability"
    )
    opposite_key = (
        "shadow_sell_success_probability" if side == "BUY"
        else "shadow_buy_success_probability"
    )
    try:
        quality = float(response.get(quality_key, -1.0))
        opposite = float(response.get(opposite_key, -1.0))
        full_sl = float(response.get("shadow_full_sl_probability", -1.0))
    except (TypeError, ValueError):
        return False

    response.update({
        "selector_candidate_direction": side,
        "selector_quality_probability": quality,
        "selector_opposite_quality_probability": opposite,
        "selector_quality_margin": quality - opposite if quality >= 0 and opposite >= 0 else -1.0,
        "selector_full_sl_probability": full_sl,
    })

    dominant_edge = max(decision.buy_edge, decision.sell_edge)
    # Do not rescue a forecast with effectively no edge. The same 25%-of-floor
    # guard already exists in the Chronos path-confirmation logic.
    if dominant_edge < max(decision.trend_edge_floor, 0.0):
        return False
    if decision.signal_strength < decision.intrabar_min_strength:
        return False
    if not (
        decision.ai_trend_confirmed
        and decision.ai_trend_direction == side
        and decision.intrabar_turn_confirmed
    ):
        return False
    if not (0.0 <= quality <= 1.0 and quality >= minimum_quality):
        return False
    if not (0.0 <= opposite <= 1.0 and quality - opposite >= minimum_quality_margin):
        return False
    if not (
        0.0 <= full_sl <= 1.0
        and full_sl <= maximum_full_sl_probability
    ):
        return False

    response["decision"] = side
    response["reason"] = "selector_v2_" + side.lower()
    response["edge"] = dominant_edge
    response["selector_live_selected"] = 1
    return True
