"""Causal trade context and explicit exits for the demo candidate selector.

Scores are transparent heuristics, not calibrated probabilities or a learned edge.
All thresholds here are hypotheses to evaluate on chronological demo replays.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Mapping

from .common import REJECTED, parse_timestamp

_INTERVALS = {"M5": 300, "H1": 3600}


def weekend_break(previous: float, following: float, interval: int) -> bool:
    """Recognize only a narrow Friday-night to Sunday-night break shape.

    This is a timestamp-pattern classification, not broker calendar verification.
    A gap extending into Monday or beginning earlier on Friday remains an outage.
    """
    closed = datetime.fromtimestamp(previous + interval, timezone.utc)
    reopened = datetime.fromtimestamp(following, timezone.utc)
    return (closed.weekday() == 4 and 21 <= closed.hour <= 23
            and reopened.weekday() == 6 and 21 <= reopened.hour <= 23
            and 46*3600 <= following-(previous+interval) <= 50*3600)


def completed_context(market: Mapping[str, Any], timeframe: str) -> tuple[list[dict], str]:
    """Accept only completed, ordered, valid context available at decision time."""
    raw = market.get("context_bars", {}).get(timeframe, [])
    if not raw:
        return [], "MISSING"
    interval = _INTERVALS[timeframe]
    try:
        cutoff = float(market.get("decision_time", parse_timestamp(market["bars"][-1]["time"]) + 900))
        if not math.isfinite(cutoff):
            raise ValueError("invalid decision time")
        bars = []
        for row in raw:
            stamp = parse_timestamp(row["time"])
            if stamp + interval > cutoff or row.get("closed") is False:
                continue
            values = {key: float(row[key]) for key in ("open", "high", "low", "close")}
            if not all(math.isfinite(v) and v > 0 for v in values.values()):
                raise ValueError("invalid OHLC")
            if not values["low"] <= min(values["open"], values["close"]) <= max(values["open"], values["close"]) <= values["high"]:
                raise ValueError("inconsistent OHLC")
            if bars and stamp <= bars[-1]["time"]:
                raise ValueError("unordered context")
            bars.append({"time": stamp, **values})
        if not bars:
            return [], "NO_COMPLETED_BARS"
        if any((b["time"]-a["time"]) % interval != 0 for a, b in zip(bars, bars[1:])):
            return [], "INVALID_CADENCE"
        if cutoff - (bars[-1]["time"] + interval) > interval * 2:
            return [], "STALE"
        # Do not infer a continuous trend across a large recent data outage.
        recent = bars[-20:]
        if any(b["time"] - a["time"] > interval * 8
               and not weekend_break(a["time"], b["time"], interval)
               for a, b in zip(recent, recent[1:])):
            return [], "GAPPED"
        return bars, "AVAILABLE"
    except (KeyError, TypeError, ValueError, OverflowError):
        return [], "INVALID"


def _ema(values: list[float], period: int) -> float:
    result = values[0]
    alpha = 2.0 / (period + 1)
    for value in values[1:]:
        result += alpha * (value - result)
    return result


def market_context(market: Mapping[str, Any]) -> dict[str, Any]:
    """H1 directional context and M5 trigger, with missing evidence explicit."""
    result = {}
    for timeframe, minimum, fast, slow in (("H1", 60, 20, 50), ("M5", 24, 8, 21)):
        bars, status = completed_context(market, timeframe)
        item = {"status": status, "bars": len(bars), "bias": "UNKNOWN", "trigger": "NONE"}
        if bars:
            breaks = [{"last_bar": a["time"], "reopened_bar": b["time"]}
                      for a, b in zip(bars[-20:], bars[-20:][1:])
                      if b["time"]-a["time"] > _INTERVALS[timeframe]*8
                      and weekend_break(a["time"], b["time"], _INTERVALS[timeframe])]
            if breaks:
                item.update(session_breaks=breaks, continuity="OBSERVED_WEEKEND_BREAK",
                            broker_calendar_verified=False)
        if status == "AVAILABLE" and len(bars) < minimum:
            item["status"] = "INSUFFICIENT_HISTORY"
        elif status == "AVAILABLE":
            closes = [bar["close"] for bar in bars[-120:]]
            fast_value, slow_value = _ema(closes, fast), _ema(closes, slow)
            slow_before = _ema(closes[:-3], slow)
            if closes[-1] > fast_value > slow_value and slow_value > slow_before:
                item["bias"] = "BUY"
            elif closes[-1] < fast_value < slow_value and slow_value < slow_before:
                item["bias"] = "SELL"
            else:
                item["bias"] = "NEUTRAL"
            if timeframe == "M5":
                if closes[-1] > max(bar["high"] for bar in bars[-4:-1]) and closes[-1] > bars[-1]["open"]:
                    item["trigger"] = "BUY"
                elif closes[-1] < min(bar["low"] for bar in bars[-4:-1]) and closes[-1] < bars[-1]["open"]:
                    item["trigger"] = "SELL"
            item.update(fast_ema=round(fast_value, 8), slow_ema=round(slow_value, 8),
                        last_completed_bar=bars[-1]["time"])
        result[timeframe] = item
    return result


def plan_target(opportunity: Mapping[str, Any], risk: dict[str, Any], market: Mapping[str, Any],
                minimum_rr: float) -> dict[str, Any]:
    """Keep the sized stop; cap a planned target at the nearest unbroken level.

    A range fade exits at its midpoint. Other setups retain their configured R
    target unless a confirmed M15/H1 swing leaves less room. Never extend a target
    through an obstacle just to manufacture an acceptable reward/risk ratio.
    """
    if not risk.get("approved"):
        return risk
    setup = opportunity.get("setup", {})
    if not setup or "stop_price" not in risk or not all(key in market for key in ("bid", "ask")):
        return risk
    side = opportunity["side"]
    sign = 1 if side == "BUY" else -1
    entry = float(market["ask"] if sign > 0 else market["bid"])
    stop = float(risk["stop_price"])
    distance = (entry - stop) * sign
    target_rr = float(risk.get("target_reward_risk") or minimum_rr)
    target = entry + sign * distance * target_rr
    source = "CONFIGURED_R_MULTIPLE"
    if setup.get("type") == "RANGE_EDGE_FADE":
        target = (float(setup["range_high"]) + float(setup["range_low"])) / 2
        source = "RANGE_MIDPOINT"
    else:
        levels = []
        h1_bars, _ = completed_context(market, "H1")
        for timeframe, bars in (("M15", market["bars"][-80:]), ("H1", h1_bars[-80:])):
            field = "high" if sign > 0 else "low"
            for i in range(3, len(bars) - 3):
                level = float(bars[i][field])
                neighbors = bars[i-3:i] + bars[i+1:i+4]
                if any(sign * float(bar[field]) >= sign * level for bar in neighbors):
                    continue
                if any(sign * (float(bar["close"]) - level) > 0 for bar in bars[i+1:]):
                    continue  # a level already broken by a close is no longer an obstacle
                if sign * (level - entry) > 0:
                    levels.append((sign * (level - entry), level, timeframe))
        if levels:
            _, level, timeframe = min(levels)
            obstacle_target = level - sign * float(setup["atr"]) * 0.10
            if sign * (obstacle_target - target) < 0:
                target, source = obstacle_target, f"{timeframe}_SWING_BEFORE_OBSTACLE"
    rr = sign * (target - entry) / distance
    result = {**risk, "checks": list(risk.get("checks", [])), "target_price": target,
              "target_source": source, "reward_risk": rr, "target_reward_risk": rr,
              "take_profit_points": sign * (target - entry) / float(market["point"])}
    passed = math.isfinite(rr) and target > 0 and rr >= minimum_rr
    result["checks"].append({"name": "target_structure", "passed": passed,
                             "detail": f"{source}: target {target:g}, available reward/risk {rr:.2f}",
                             "metrics": {"target_price": target, "reward_risk": rr, "minimum": minimum_rr}})
    if not passed:
        result.update(approved=False, status=REJECTED, reason="TARGET_ROOM_TOO_SMALL", volume=0.0,
                      explanation="The nearest structural target does not leave the minimum reward/risk.")
    return result


def rank_candidate(opportunity: Mapping[str, Any], timing: Mapping[str, Any], risk: Mapping[str, Any],
                   context: Mapping[str, Any]) -> dict[str, Any]:
    """Rank executable plans; missing timeframes are neutral, not confirmation."""
    side = opportunity.get("side")
    h1, m5 = context["H1"], context["M5"]
    h1_score = 0.5 if h1["bias"] in {"UNKNOWN", "NEUTRAL"} else 1.0 if h1["bias"] == side else 0.0
    m5_score = 1.0 if m5["trigger"] == side else 0.0 if m5["trigger"] != "NONE" else (
        0.75 if m5["bias"] == side else 0.25 if m5["bias"] not in {"UNKNOWN", "NEUTRAL"} else 0.5)
    components = {"setup": float(opportunity.get("score", 0)),
                  "entry": float(timing.get("quality", 0)), "h1_alignment": h1_score,
                  "m5_trigger": m5_score, "target_room": min(1.0, max(0.0, float(risk.get("reward_risk") or 0) / 3.0))}
    weights = {"setup": 0.40, "entry": 0.25, "h1_alignment": 0.15, "m5_trigger": 0.10, "target_room": 0.10}
    score = sum(weights[key] * min(1.0, max(0.0, value)) for key, value in components.items())
    return {"score": round(score, 6), "components": components, "weights": weights,
            "interpretation": "HEURISTIC_RANK_NOT_WIN_PROBABILITY"}
