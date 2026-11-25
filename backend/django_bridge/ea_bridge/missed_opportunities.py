"""Offline per-signal counterfactuals; no orders, sizing overrides or database writes."""
import math
from collections import Counter


def audit_rejection(result, market, future, *, horizon=32, slippage_points=0):
    stages = result.get("stages") or {}
    risk = stages.get("risk_management") or {}
    op = stages.get("opportunities") or {}
    timing = stages.get("entry_timing") or {}
    reason = risk.get("reason")
    if reason not in ("RISK_BUDGET_BELOW_MINIMUM_VOLUME", "TARGET_ROOM_TOO_SMALL"):
        return None
    if not op.get("approved") or not timing.get("approved"):
        return None
    evidence = {"reason": reason, "side": op.get("side"),
                "signal_at": market["latest_bar_time"],
                "target_source": risk.get("target_source"),
                "available_rr": risk.get("reward_risk"),
                "minimum_required_risk_percent": risk.get("minimum_required_risk_percent"),
                "stop": risk.get("stop_price"), "structural_target": risk.get("target_price")}
    # For rejected sizing, no approved structural TP exists. Model a configured-R
    # hypothetical only, and label it separately from the observed obstacle.
    try:
        side = evidence["side"]
        if side not in ("BUY", "SELL"):
            raise ValueError()
        sign = 1 if side == "BUY" else -1
        entry = float(market["ask"] if sign == 1 else market["bid"])
        stop = float(risk["stop_price"])
        spread = float(market["ask"]) - float(market["bid"])
        slip = float(slippage_points) * float(market["point"])
        rr = float(market["target_reward_risk"])
        target = entry + sign * abs(entry-stop) * rr
        if not all(math.isfinite(v) for v in (entry, stop, target, spread, slip, rr)) or min(entry,stop,target,rr)<=0 or spread<0 or slip<0 or sign*(entry-stop)<=0:
            raise ValueError()
    except (KeyError, ValueError, TypeError, OverflowError):
        return {**evidence, "outcome": "UNAVAILABLE_PLAN"}
    evidence.update(hypothetical_target=target, target_policy="CONFIGURED_R_IGNORING_OBSTACLE",
                    horizon_bars=horizon)
    previous = market["latest_bar_time"]
    fill = None
    for index, bar in enumerate(future[:horizon]):
        if bar["time"] != previous + 900:
            return {**evidence, "outcome": "DATA_GAP"}
        previous = bar["time"]
        if index == 0:
            fill = bar["open"] + (spread + slip if sign == 1 else -slip)
            if not (sign*(fill-stop)>0 and sign*(target-fill)>0):
                return {**evidence, "outcome": "INVALID_NEXT_OPEN"}
            evidence["hypothetical_entry"] = fill
        adjustment = 0 if sign == 1 else spread
        stop_hit = bar["low"] <= stop if sign == 1 else bar["high"]+adjustment >= stop
        target_hit = bar["high"] >= target if sign == 1 else bar["low"]+adjustment <= target
        if stop_hit or target_hit:
            opening = bar["open"] + adjustment
            level = (min(stop,opening) if sign == 1 else max(stop,opening)) if stop_hit else target
            exit_price = level - sign*slip
            return {**evidence, "outcome": "STOP" if stop_hit else "TARGET",
                    "bars_to_outcome": index+1,
                    "gross_r": round(sign*(exit_price-fill)/abs(fill-stop),6)}
    return {**evidence, "outcome": "HORIZON_UNRESOLVED" if len(future)>=horizon else "END_OF_DATA"}


def summarize_audit(records):
    groups = {}
    for reason in sorted({r["reason"] for r in records}):
        rows = [r for r in records if r["reason"] == reason]
        resolved = [r["gross_r"] for r in rows if "gross_r" in r]
        groups[reason] = {"observations": len(rows),
                          "outcomes": dict(Counter(r["outcome"] for r in rows)),
                          "target_sources": dict(Counter(r["target_source"] or "UNAVAILABLE" for r in rows)),
                          "mean_gross_r_resolved_only": round(sum(resolved)/len(resolved),6) if resolved else None,
                          "examples": rows[:10]}
    return {"mode": "COUNTERFACTUAL_ONLY", "groups": groups,
            "limitations": "Overlapping per-bar observations, not independent trades or portfolio PnL. Configured-R target ignores structural obstacles. Next-open fills, fixed spread, adverse slippage, stop first, no commission; gaps and unresolved outcomes excluded from mean R."}
