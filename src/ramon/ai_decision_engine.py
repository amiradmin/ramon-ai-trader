"""Direction-first live decision engine for Ramon.

BUY/SELL quality specialists choose the analytical direction. Chronos remains an
input/confirmation source, while market-state hazards and execution safety gates
remain authoritative. If directional quality is weak or ambiguous, the engine
returns WAIT.
"""
from __future__ import annotations

from math import isfinite

from .core import Decision


def _p(value: object, default: float = 0.5) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if isfinite(result) and 0.0 <= result <= 1.0 else default


def apply_ai_decision_engine(
    response: dict,
    decision: Decision,
    assessment: dict,
    *,
    enabled: bool,
    minimum_score: float = 0.56,
    minimum_direction_quality: float = 0.55,
    minimum_quality_margin: float = 0.05,
    maximum_full_sl_probability: float = 0.50,
    anomaly_soft_threshold: float = 2.0,
    anomaly_hard_threshold: float = 5.0,
) -> bool:
    """Choose BUY/SELL/WAIT from learned directional quality and specialists."""
    response.update({
        "ai_engine_v2_enabled": int(enabled),
        "ai_engine_v2_active": 0,
        "ai_engine_v2_selected": 0,
        "ai_engine_v2_minimum_score": minimum_score,
        "ai_engine_v2_minimum_direction_quality": minimum_direction_quality,
        "ai_engine_v2_minimum_quality_margin": minimum_quality_margin,
        "ai_engine_v2_maximum_full_sl_probability": maximum_full_sl_probability,
    })
    if not enabled:
        return False

    # Hard market hazards stay authoritative.
    if assessment.get("route") == "WAIT":
        response.update(
            decision="WAIT",
            reason="ai_engine_v2_market_hazard",
            ai_engine_v2_block="market_hazard_route",
        )
        return True

    buy_quality = _p(response.get("shadow_buy_success_probability"), -1.0)
    sell_quality = _p(response.get("shadow_sell_success_probability"), -1.0)
    entry_probability = _p(response.get("entry_probability"), -1.0)
    regime_probability = _p(response.get("regime_probability"), -1.0)
    news_probability = _p(response.get("news_probability"), -1.0)
    meta_probability = _p(response.get("meta_probability"), -1.0)
    full_sl = _p(response.get("shadow_full_sl_probability"), -1.0)

    required = (buy_quality, sell_quality, entry_probability, full_sl)
    if any(value < 0.0 for value in required):
        response.update(
            decision="WAIT",
            reason="ai_engine_v2_models_unavailable",
            ai_engine_v2_block="specialist_model_unavailable",
        )
        return True

    selected = "BUY" if buy_quality > sell_quality else "SELL"
    side_quality = max(buy_quality, sell_quality)
    opposite_quality = min(buy_quality, sell_quality)
    quality_margin = side_quality - opposite_quality

    response.update({
        "ai_engine_v2_active": 1,
        "ai_engine_v2_candidate_direction": selected,
        "ai_engine_v2_direction_source": "direction_quality_live",
        "ai_engine_v2_buy_quality": buy_quality,
        "ai_engine_v2_sell_quality": sell_quality,
        "ai_engine_v2_quality_margin": quality_margin,
        "ai_engine_v2_entry_probability": entry_probability,
        "ai_engine_v2_full_sl_probability": full_sl,
    })

    if side_quality < minimum_direction_quality:
        response.update(
            decision="WAIT",
            reason="ai_engine_v2_direction_quality_low",
            ai_engine_v2_block="direction_quality_low",
        )
        return True
    if quality_margin < minimum_quality_margin:
        response.update(
            decision="WAIT",
            reason="ai_engine_v2_direction_ambiguous",
            ai_engine_v2_block="direction_quality_margin",
        )
        return True

    allowed = set(assessment.get("allowed_directions", ()))
    if allowed and selected not in allowed:
        response.update(
            decision="WAIT",
            reason="ai_engine_v2_market_direction_block",
            ai_engine_v2_block="market_direction_not_allowed",
        )
        return True

    # A confirmed independent market direction may veto the learned side, but
    # neutral timing does not force WAIT. This keeps direction live without
    # restoring the old Chronos-direction dependency.
    independent_direction = str(response.get("market_direction", "NEUTRAL"))
    if independent_direction in {"BUY", "SELL"} and independent_direction != selected:
        response.update(
            decision="WAIT",
            reason="ai_engine_v2_independent_direction_conflict",
            ai_engine_v2_block="independent_direction_conflict",
        )
        return True

    if full_sl > maximum_full_sl_probability:
        response.update(
            decision="WAIT",
            reason="ai_engine_v2_risk_veto",
            ai_engine_v2_block="full_sl_probability",
        )
        return True

    safe_probability = 1.0 - full_sl
    regime_component = regime_probability if regime_probability >= 0.0 else 0.5
    news_component = news_probability if news_probability >= 0.0 else 0.5

    if meta_probability >= 0.0:
        score = (
            0.40 * meta_probability
            + 0.25 * side_quality
            + 0.15 * entry_probability
            + 0.12 * safe_probability
            + 0.05 * news_component
            + 0.03 * regime_component
        )
        score_source = "direction_live_meta_plus_specialists"
    else:
        score = (
            0.42 * side_quality
            + 0.25 * entry_probability
            + 0.20 * safe_probability
            + 0.08 * news_component
            + 0.05 * regime_component
        )
        score_source = "direction_live_specialist_consensus"

    anomaly_ratio = -1.0
    try:
        anomaly_ratio = float(response.get("moment_anomaly_ratio", -1.0))
    except (TypeError, ValueError):
        pass
    anomaly_penalty = 0.0
    if isfinite(anomaly_ratio) and anomaly_ratio >= anomaly_soft_threshold:
        anomaly_penalty = min(
            0.18,
            max(0.0, anomaly_ratio - anomaly_soft_threshold) * 0.04,
        )
    effective_score = score - anomaly_penalty

    response.update({
        "ai_engine_v2_score_source": score_source,
        "ai_engine_v2_raw_score": score,
        "ai_engine_v2_score": effective_score,
        "ai_engine_v2_regime_probability": regime_component,
        "ai_engine_v2_news_probability": news_component,
        "ai_engine_v2_safe_probability": safe_probability,
        "ai_engine_v2_meta_probability": meta_probability,
        "ai_engine_v2_anomaly_ratio": anomaly_ratio,
        "ai_engine_v2_anomaly_penalty": anomaly_penalty,
    })

    if isfinite(anomaly_ratio) and anomaly_ratio >= anomaly_hard_threshold:
        response.update(
            decision="WAIT",
            reason="ai_engine_v2_extreme_anomaly",
            ai_engine_v2_block="extreme_anomaly",
        )
        return True

    if effective_score < minimum_score:
        response.update(
            decision="WAIT",
            reason="ai_engine_v2_veto",
            ai_engine_v2_block="score_below_threshold",
        )
        return True

    # Keep execution geometry from the analytical snapshot; only direction
    # ownership moves to the learned classifier.
    chosen_edge = decision.buy_edge if selected == "BUY" else decision.sell_edge
    response.update(
        decision=selected,
        reason="ai_engine_v2_" + selected.lower(),
        edge=max(chosen_edge, 0.0),
        ai_engine_v2_selected=1,
        ai_engine_v2_block="",
    )
    return True
