"""Chronological, single-position demo replay with explicit execution assumptions.

Decide using completed candles, enter on the next candle's opening quote, then
inspect future candles only to resolve the outcome. This is an OHLC approximation,
not an MT5 tick backtest, a parameter optimizer, or a trading command producer.
"""
from bisect import bisect_right
from collections import Counter
import math
from typing import Any, Callable, Mapping, Sequence

from .pipeline import SignalFlowPipeline


def replay_strategy(pipeline_factory: Callable[[], SignalFlowPipeline], bars: Sequence[dict], *,
                    context_bars: Mapping[str, Sequence[dict]], account: Mapping[str, Any],
                    spread_points: float, slippage_points: float, commission_points: float,
                    max_holding_bars: int = 24, warmup_bars: int = 128) -> dict[str, Any]:
    """Compare fixed policies on the same data and costs, without fitting to outcomes."""
    costs = (spread_points, slippage_points, commission_points)
    if not all(math.isfinite(value) and value >= 0 for value in costs):
        raise ValueError("execution costs must be finite and non-negative")
    if warmup_bars < 128 or max_holding_bars < 1:
        raise ValueError("at least 128 warmup bars and one holding bar are required")
    point, point_value = float(account["point"]), float(account["point_value_per_lot"])
    initial_balance = balance = float(account["account_balance"])
    if min(point, point_value, balance) <= 0:
        raise ValueError("point, point value and account balance must be positive")
    for i, bar in enumerate(bars):
        values = [float(bar[key]) for key in ("time", "open", "high", "low", "close")]
        if not all(math.isfinite(value) and value > 0 for value in values):
            raise ValueError("replay candles must be finite and positive")
        if bar["time"] % 900 != 0:
            raise ValueError("M15 replay candles must use the UTC 15-minute grid; exclude malformed history explicitly")
        if not bar["low"] <= min(bar["open"], bar["close"]) <= max(bar["open"], bar["close"]) <= bar["high"]:
            raise ValueError("inconsistent replay OHLC")
        if i and bar["time"] <= bars[i-1]["time"]:
            raise ValueError("replay candles must be strictly chronological")
    context_times = {tf: [bar["time"] for bar in rows] for tf, rows in context_bars.items()}
    if any(any(b <= a for a, b in zip(times, times[1:])) for times in context_times.values()):
        raise ValueError("context candles must be strictly chronological")
    trades = []
    reasons = Counter()
    context_status_counts = Counter()
    peak, max_drawdown = balance, 0.0
    decisions = 0
    i = warmup_bars - 1
    while i < len(bars)-1 and balance > 0:
        current, next_bar = bars[i], bars[i+1]
        cutoff = current["time"] + 900
        if next_bar["time"] != cutoff:
            reasons["NEXT_OPEN_GAP"] += 1
            i += 1
            continue
        pipeline = pipeline_factory()
        spread = spread_points * point
        atr = sum(max(bars[j]["high"]-bars[j]["low"], abs(bars[j]["high"]-bars[j-1]["close"]),
                      abs(bars[j]["low"]-bars[j-1]["close"])) for j in range(i-13, i+1))/14
        market = {**account, "account_balance": balance, "bars": list(bars[max(0, i-499):i+1]),
                  "last_bar_closed": True, "latest_bar_time": current["time"], "decision_time": cutoff,
                  "symbol": account.get("symbol", "XAUUSD"), "timeframe": "M15",
                  "bid": next_bar["open"], "ask": next_bar["open"] + spread,
                  "spread_points": spread_points, "entry_quality": min(1.0, abs(current["close"]-bars[i-4]["close"]) /
                      (max(atr, point)*1.5)),
                  "risk_percent": pipeline.risk_manager.maximum_risk_percent,
                  "target_reward_risk": pipeline.risk_manager.config.target_reward_risk,
                  "open_risk_percent": 0.0, "context_bars": {}}
        for timeframe, interval in (("H1", 3600), ("M5", 300)):
            rows = context_bars.get(timeframe, [])
            end = bisect_right(context_times.get(timeframe, []), cutoff-interval)
            market["context_bars"][timeframe] = list(rows[max(0, end-120):end])
        result = pipeline.run(market)
        decisions += 1
        for timeframe, status in result.get("selection", {}).get("context", {}).items():
            context_status_counts[f"{timeframe}:{status['status']}"] += 1
        if not result["approved"]:
            reasons[result["reason"]] += 1
            i += 1
            continue
        risk = result["stages"]["risk_management"]
        opportunity = result["stages"]["opportunities"]
        sign = 1 if result["action"] == "BUY" else -1
        entry = market["ask"] if sign > 0 else market["bid"]
        stop = risk["stop_price"]
        distance = (entry-stop)*sign
        target = risk.get("target_price", entry+sign*distance*market["target_reward_risk"])
        if opportunity.get("setup", {}).get("type") == "RANGE_EDGE_FADE":
            setup = opportunity["setup"]
            target = (setup["range_high"]+setup["range_low"])/2
        if distance <= 0 or sign*(target-entry)/distance < pipeline.risk_manager.config.min_reward_risk:
            reasons["TARGET_ROOM_TOO_SMALL"] += 1
            i += 1
            continue
        fill = entry + sign*slippage_points*point
        if sign*(fill-stop) <= 0 or sign*(target-fill) <= 0:
            reasons["SLIPPAGE_INVALIDATES_PLAN"] += 1
            i += 1
            continue
        exit_index = min(i+max_holding_bars, len(bars)-1)
        exit_reason = "TIME_EXIT"
        for j in range(i+1, exit_index+1):
            bar = bars[j]
            # MT5 bid candles: SELL exits are bought at ask; BUY exits sell at bid.
            adjustment = spread if sign < 0 else 0.0
            opened, low, high = (bar[key]+adjustment for key in ("open", "low", "high"))
            stop_hit = low <= stop if sign > 0 else high >= stop
            target_hit = high >= target if sign > 0 else low <= target
            if sign*(opened-stop) <= 0:
                exit_price, exit_index, exit_reason = opened, j, "STOP_GAP"
                break
            if sign*(opened-target) >= 0:
                exit_price, exit_index, exit_reason = target, j, "TARGET"
                break
            if stop_hit:
                exit_price = min(stop, opened) if sign > 0 else max(stop, opened)
                exit_index, exit_reason = j, "STOP"  # conservative if both levels trade in one candle
                break
            if target_hit:
                exit_price = target  # do not assume favorable limit-order gap improvement
                exit_index, exit_reason = j, "TARGET"
                break
        else:
            exit_price = bars[exit_index]["close"] + (spread if sign < 0 else 0.0)
        exit_price -= sign*slippage_points*point
        net_move = sign*(exit_price-fill)-commission_points*point
        net_r = net_move/distance
        cash_risk = float(risk["volume"])*distance/point*point_value
        pnl = net_r*cash_risk
        balance += pnl
        peak = max(peak, balance)
        max_drawdown = max(max_drawdown, (peak-balance)/peak*100)
        trades.append({"entry_time": next_bar["time"], "exit_time": bars[exit_index]["time"]+900,
                       "setup": opportunity.get("setup", {}).get("type", "UNKNOWN"), "side": result["action"],
                       "entry": fill, "stop": stop, "target": target, "exit": exit_price,
                       "exit_reason": exit_reason, "net_r": round(net_r, 6), "pnl": round(pnl, 6)})
        i = exit_index
    total_r = sum(trade["net_r"] for trade in trades)
    return {"decisions": decisions, "trades": trades, "trade_count": len(trades),
            "net_r": round(total_r, 6), "expectancy_r": round(total_r/len(trades), 6) if trades else None,
            "win_rate": sum(t["net_r"] > 0 for t in trades)/len(trades) if trades else None,
            "initial_balance": initial_balance, "ending_balance": round(balance, 6),
            "max_closed_equity_drawdown_percent": round(max_drawdown, 6), "skip_reasons": dict(reasons),
            "context_status_counts": dict(context_status_counts),
            "assumptions": {"spread_points": spread_points, "slippage_points_each_side": slippage_points,
                            "round_trip_commission_points": commission_points, "max_holding_bars": max_holding_bars,
                            "same_bar_stop_and_target": "STOP_FIRST", "entry": "NEXT_OPEN",
                            "positions": "ONE_AT_A_TIME", "costs": "FIXED_ASSUMPTIONS_NOT_HISTORICAL_QUOTES",
                            "drawdown": "CLOSED_EQUITY_ONLY_NOT_INTRATRADE", "account": dict(account)}}
