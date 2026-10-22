"""Opt-in execution bridge for independent price-structure opportunities.

This module only changes a final WAIT into BUY/SELL for a well-formed setup.
Actual order placement, sizing, news calendar and account limits remain in MT5 EA.
"""
from __future__ import annotations
from math import isfinite
from .market_opportunity_engine import Opportunity


def apply_live_opportunity(response: dict, opportunity: Opportunity, *,
    enabled: bool, ea_capable: bool, fresh_quote: bool, assessment: dict,
    max_spread_ok: bool, manual_override: bool = False) -> bool:
    response["opportunity_live_enabled"] = int(enabled)
    response["opportunity_live_selected"] = 0
    def reject(reason: str) -> bool:
        response["opportunity_live_reason"] = reason
        return False
    if not enabled:
        return reject("disabled")
    if not ea_capable or not fresh_quote:
        return reject("ea_not_ready_or_stale_quote")
    if manual_override or response.get("range_execution") or response.get("reversal_execution"):
        return reject("another_execution_lane")
    if response.get("decision") != "WAIT":
        return reject("existing_decision_retained")
    if assessment.get("route") == "WAIT" or not max_spread_ok:
        return reject("market_hazard_or_spread")
    if int(response.get("moment_live_veto", 0) or 0):
        return reject("anomaly_veto")
    if opportunity.decision not in {"BUY", "SELL"}:
        return reject("no_valid_setup")
    allowed = assessment.get("allowed_directions") or []
    if allowed and opportunity.decision not in allowed:
        return reject("market_direction_not_allowed")
    independent = response.get("market_direction")
    if independent in {"BUY", "SELL"} and independent != opportunity.decision:
        return reject("independent_direction_conflict")
    if not all(isfinite(v) and v > 0 for v in (opportunity.entry, opportunity.stop,
                                                opportunity.target, opportunity.reward_risk)):
        return reject("invalid_geometry")
    if not (opportunity.stop < opportunity.entry < opportunity.target
            if opportunity.decision == "BUY"
            else opportunity.target < opportunity.entry < opportunity.stop):
        return reject("invalid_geometry")
    stop_distance = abs(opportunity.entry - opportunity.stop)
    target_distance = abs(opportunity.target - opportunity.entry)
    if stop_distance <= 0 or target_distance / stop_distance < 1.5 - 1e-9:
        return reject("reward_risk_too_low")
    response.update({
        "decision": opportunity.decision,
        "reason": "structure_" + opportunity.setup.lower(),
        "stop_distance": stop_distance,
        "target_distance": target_distance,
        "legacy_target_price": opportunity.target,
        "target_structure_ready": 0,
        "target_direction": opportunity.decision,
        "target_method": "structure_opportunity_v1",
        "target_tp1": 0.0, "target_tp2": 0.0, "target_tp3": 0.0,
        "opportunity_live_selected": 1,
        "opportunity_live_reason": "selected",
    })
    return True
