"""AI-first analytical decision engine for Ramon.

Specialist learned probabilities replace hard PASS/FAIL analytical gates. The
engine never bypasses market-state hazard routes or terminal/account execution
safety. It can recover selected analytical WAIT reasons and can veto otherwise
valid analytical entries when the learned score is weak.
"""
from __future__ import annotations

from math import isfinite

from .core import Decision
from .ensemble import dominant_direction


RECOVERABLE_REASONS = {
    "insufficient_model_edge",
    "insufficient_model_strength",
    "direction_confirmation_required",
}

NON_RECOVERABLE_REASONS = {
    "spread_or_atr",
    "trend_conflict",
    "adverse_intrabar_timing",
    "late_entry_extension",
}


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
    minimum_quality_margin: float = 0.03,
    anomaly_soft_threshold: float = 2.0,
    anomaly_hard_threshold: float = 5.0,
) -> bool:
    """Set response decision from learned specialist scores.

    Returns True only when the AI engine becomes authoritative for the analytical
    decision. A False return means the legacy analytical path remains in force.
    """
    response.update({
        "ai_engine_v2_enabled": int(enabled),
        "ai_engine_v2_active": 0,
        "ai_engine_v2_selected": 0,
        "ai_engine_v2_minimum_score": minimum_score,
        "ai_engine_v2_minimum_quality_margin": minimum_quality_margin,
    })
    if not enabled:
        return False

    # Market-state WAIT contains hard hazard/unknown states. The AI engine may
    # score them for telemetry later, but it cannot create a live order there.
    if assessment.get("route") == "WAIT":
        response["ai_engine_v2_block"] = "market_hazard_route"
        return False

    base_reason = str(decision.reason)
    base_side = dominant_direction(decision)
    current = str(response.get("decision", "WAIT"))
    if base_side not in {"BUY", "SELL"}:
        response["ai_engine_v2_block"] = "no_model_direction"
        return False
    if base_reason in NON_RECOVERABLE_REASONS:
        response["ai_engine_v2_block"] = base_reason
        return False
    if current == "WAIT" and base_reason not in RECOVERABLE_REASONS:
        response["ai_engine_v2_block"] = "nonrecoverable_wait"
        return False

    buy_quality = _p(response.get("shadow_buy_success_probability"), -1.0)
    sell_quality = _p(response.get("shadow_sell_success_probability"), -1.0)
    entry_probability = _p(response.get("entry_probability"), -1.0)
    regime_probability = _p(response.get("regime_probability"), -1.0)
    news_probability = _p(response.get("news_probability"), -1.0)
    meta_probability = _p(response.get("meta_probability"), -1.0)
    full_sl = _p(response.get("shadow_full_sl_probability"), -1.0)

    required = (buy_quality, sell_quality, entry_probability, full_sl)
    if any(value < 0.0 for value in required):
        response["ai_engine_v2_block"] = "specialist_model_unavailable"
        return False

    side_quality = buy_quality if base_side == "BUY" else sell_quality
    opposite_quality = sell_quality if base_side == "BUY" else buy_quality
    quality_margin = side_quality - opposite_quality
    if quality_margin < minimum_quality_margin:
        response.update({
            "ai_engine_v2_block": "direction_quality_margin",
            "ai_engine_v2_quality_margin": quality_margin,
        })
        return False

    safe_probability = 1.0 - full_sl
    # Missing optional specialists are neutral rather than silently blocking.
    regime_component = regime_probability if regime_probability >= 0.0 else 0.5
    news_component = news_probability if news_probability >= 0.0 else 0.5

    # Until the learned meta role has enough purged samples, combine learned
    # specialist probabilities. Once meta is ready it becomes the largest input.
    if meta_probability >= 0.0:
        score = (
            0.45 * meta_probability
            + 0.22 * side_quality
            + 0.15 * entry_probability
            + 0.12 * safe_probability
            + 0.04 * news_component
            + 0.02 * regime_component
        )
        score_source = "learned_meta_plus_specialists"
    else:
        score = (
            0.38 * side_quality
            + 0.27 * entry_probability
            + 0.22 * safe_probability
            + 0.08 * news_component
            + 0.05 * regime_component
        )
        score_source = "specialist_consensus"

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
        "ai_engine_v2_active": 1,
        "ai_engine_v2_candidate_direction": base_side,
        "ai_engine_v2_score_source": score_source,
        "ai_engine_v2_raw_score": score,
        "ai_engine_v2_score": effective_score,
        "ai_engine_v2_buy_quality": buy_quality,
        "ai_engine_v2_sell_quality": sell_quality,
        "ai_engine_v2_quality_margin": quality_margin,
        "ai_engine_v2_entry_probability": entry_probability,
        "ai_engine_v2_regime_probability": regime_component,
        "ai_engine_v2_news_probability": news_component,
        "ai_engine_v2_full_sl_probability": full_sl,
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

    response.update(
        decision=base_side,
        reason="ai_engine_v2_" + base_side.lower(),
        edge=max(decision.buy_edge, decision.sell_edge),
        ai_engine_v2_selected=1,
        ai_engine_v2_block="",
    )
    return True
