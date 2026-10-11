"""Auditable, shadow-only decision safety annotations.

These checks are deliberately unable to create, reverse, or block an order.
They make data quality and a proposed no-trade decision observable first; a
future live promotion must be backed by replay and out-of-sample evidence.
"""
from __future__ import annotations

from math import isfinite

from .core import Market


def assess_data_quality(
    market: Market, *, quote_time: int | None, max_spread_points: int,
) -> dict[str, object]:
    """Describe completeness of one accepted market request without mutating it."""
    gaps = sum(
        b.time - a.time != 900
        for a, b in zip(market.bars[-65:-1], market.bars[-64:])
    )
    spread_points = (market.ask - market.bid) / market.point
    quote_age = None if quote_time is None else quote_time - (market.bars[-1].time + 900)
    reasons: list[str] = []
    if len(market.bars) < 256:
        reasons.append("short_context")
    if gaps:
        reasons.append("m15_gap")
    if spread_points > max_spread_points:
        reasons.append("wide_spread")
    if quote_time is None:
        reasons.append("quote_time_missing")
    elif quote_age is None or quote_age < 0 or quote_age > 120:
        reasons.append("quote_age_outside_shadow_window")
    if not market.micro_bars:
        reasons.append("micro_context_missing")
    return {
        "data_quality_shadow_version": "v1",
        "data_quality_shadow_ready": int(not reasons),
        "data_quality_shadow_reasons": reasons,
        "data_quality_shadow_m15_gaps_64": gaps,
        "data_quality_shadow_context_bars": len(market.bars),
        "data_quality_shadow_spread_points": round(spread_points, 4),
        "data_quality_shadow_quote_age_seconds": quote_age,
        "data_quality_shadow_micro_bars": len(market.micro_bars),
        "data_quality_shadow_authoritative": 0,
    }


def no_trade_recommendation(
    response: dict[str, object], quality: dict[str, object], assessment: dict[str, object],
    *, minimum_margin: float = 0.05,
) -> dict[str, object]:
    """Produce a recommendation only; the caller must preserve live decision."""
    original = response.get("decision")
    reasons: list[str] = []
    if not quality.get("data_quality_shadow_ready"):
        reasons.append("data_quality")
    if assessment.get("route") == "WAIT":
        reasons.append("market_hazard")
    try:
        buy, sell = float(response.get("buy_success_probability")), float(response.get("sell_success_probability"))
    except (TypeError, ValueError):
        buy = sell = float("nan")
    if not (isfinite(buy) and isfinite(sell) and 0 <= buy <= 1 and 0 <= sell <= 1):
        reasons.append("direction_probability_unavailable")
    elif abs(buy - sell) < minimum_margin:
        reasons.append("direction_ambiguous")
    candidate = "BUY" if buy > sell else "SELL" if sell > buy else "WAIT"
    recommended = "WAIT" if reasons else candidate
    payload = {
        "no_trade_shadow_version": "v1",
        "no_trade_shadow_recommendation": recommended,
        "no_trade_shadow_reasons": reasons,
        "no_trade_shadow_candidate_direction": candidate,
        "no_trade_shadow_authoritative": 0,
    }
    response.update(payload)
    assert response.get("decision") == original
    return payload
