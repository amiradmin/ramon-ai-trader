"""Counterfactual Trend Pullback exits. Research only: never imports EA commands.

Each baseline fill is replayed independently from its historical entry. These
conditional scenarios can overlap and are NOT independent portfolio backtests.
Stop updates happen after a completed bar and apply starting with the next bar.
"""
from math import isfinite

VARIANTS = ("FIXED", "BREAKEVEN_1R", "ATR_TRAIL", "HYBRID")


def _atr(bars, idx, periods=14):
    if idx < periods:
        return None
    spans = []
    for i in range(idx - periods + 1, idx + 1):
        candle, previous = bars[i], bars[i - 1]
        spans.append(max(candle["high"] - candle["low"],
                         abs(candle["high"] - previous["close"]),
                         abs(candle["low"] - previous["close"])))
    return sum(spans) / periods


def replay_exit(bars, trade, variant, *, spread, slippage, price_per_lot,
                commission_per_lot, time_exit_bars=32):
    """Simulate one existing entry without using later signal/portfolio decisions.

    Conservative OHLC policy: opening gaps fill at the worse opening price,
    stops take precedence when both stop and target fit the same candle,
    and the entry candle uses the originally submitted stop/target.
    """
    if variant not in VARIANTS:
        raise ValueError("Unknown research exit variant")
    side = trade["side"]
    if side not in ("BUY", "SELL"):
        raise ValueError("Invalid trade side")
    direction = 1 if side == "BUY" else -1
    entry = float(trade["entry"])
    stop = float(trade["stop"])
    target = float(trade["target"])
    volume = float(trade["volume"])
    risk = (entry - stop) * direction
    if not all(isfinite(v) for v in (entry, stop, target, volume, risk)):
        raise ValueError("Nonfinite position")
    if risk <= 0 or (target - entry) * direction <= 0 or volume <= 0:
        raise ValueError("Invalid initial risk or target")
    opened = int(trade["opened_at"])
    start = next((i for i, b in enumerate(bars) if b["time"] == opened), None)
    if start is None:
        return {"status": "ENTRY_BAR_UNAVAILABLE"}
    initial_stop = stop
    modifications = 0
    gaps_crossed = 0
    for i in range(start, len(bars)):
        candle = bars[i]
        if i > start:
            previous = bars[i - 1]
            if candle["time"] - previous["time"] != 900:
                gaps_crossed += 1
                # Never infer a trailing update or precise fill across missing bars.
                return {"status": "DATA_GAP", "gaps_crossed": gaps_crossed}
        low = candle["low"] if side == "BUY" else candle["low"] + spread
        high = candle["high"] if side == "BUY" else candle["high"] + spread
        market_open = candle["open"] if side == "BUY" else candle["open"] + spread
        stop_hit = low <= stop if side == "BUY" else high >= stop
        target_hit = high >= target if side == "BUY" else low <= target
        reason = "STOP" if stop_hit else "TARGET" if target_hit else None
        if reason:
            level = stop if stop_hit else target
            # Gap-through-stop: adverse price. Favorable TP opening gap not credited.
            if stop_hit:
                level = min(level, market_open) if side == "BUY" else max(level, market_open)
            exit_price = level - direction * slippage
        elif variant == "HYBRID" and i - start + 1 >= time_exit_bars:
            reason = "TIME_EXIT"
            exit_price = candle["close"] + (spread if side == "SELL" else 0) - direction * slippage
        else:
            # Completed candle is only used to modify stop for *next* bar.
            if i == len(bars) - 1:
                break
            if variant != "FIXED":
                favorable = (candle["close"] - entry) * direction
                candidate = None
                if variant in ("BREAKEVEN_1R", "HYBRID") and favorable >= risk:
                    candidate = entry
                if variant in ("ATR_TRAIL", "HYBRID") and favorable >= risk:
                    atr = _atr(bars, i)
                    if atr is not None and atr > 0:
                        trailing = candle["close"] - direction * (1.5 * atr)
                        candidate = (max(candidate, trailing) if side == "BUY"
                                     else min(candidate, trailing)) if candidate is not None else trailing
                if candidate is not None and ((candidate > stop and side == "BUY")
                                             or (candidate < stop and side == "SELL")):
                    # Do not move the stop past the observed close.
                    if (candidate - candle["close"]) * direction < 0:
                        stop = candidate
                        modifications += 1
            continue
        gross = (exit_price - entry) * direction * price_per_lot * volume
        net = gross - commission_per_lot * volume
        return {"status": "CLOSED", "reason": reason, "exit_at": candle["time"],
                "exit": round(exit_price, 5), "pnl": round(net, 2),
                "r_multiple": round(net / (risk * price_per_lot * volume), 4),
                "modifications": modifications, "initial_stop": initial_stop,
                "final_stop": round(stop, 5), "gaps_crossed": gaps_crossed}
    return {"status": "OPEN_AT_END", "modifications": modifications}


def compare_exits(bars, baseline_trades, data):
    """Conditional per-entry comparisons; deliberately not portfolio P&L."""
    spread = float(data["spread_points"]) * float(data["point"])
    slippage = float(data.get("slippage_points", 0)) * float(data["point"])
    price_per_lot = float(data["tick_value"]) / float(data["tick_size"])
    commission = float(data.get("commission_per_lot", 0))
    variants = {}
    for name in VARIANTS:
        outcomes = [replay_exit(bars, t, name, spread=spread, slippage=slippage,
                                price_per_lot=price_per_lot, commission_per_lot=commission)
                    for t in baseline_trades]
        closed = [o for o in outcomes if o["status"] == "CLOSED"]
        wins = [o["pnl"] for o in closed if o["pnl"] > 0]
        losses = [o["pnl"] for o in closed if o["pnl"] < 0]
        variants[name] = {
            "entries": len(outcomes), "closed": len(closed),
            "unresolved": len(outcomes) - len(closed),
            "win_rate_percent": round(100 * len(wins) / len(closed), 2) if closed else None,
            "conditional_net_pnl": round(sum(o["pnl"] for o in closed), 2),
            "mean_r": round(sum(o["r_multiple"] for o in closed) / len(closed), 4) if closed else None,
            "profit_factor": round(sum(wins) / abs(sum(losses)), 4) if losses else None,
            "modifications": sum(o.get("modifications", 0) for o in outcomes),
            "unresolved_by_reason": {s: sum(o["status"] == s for o in outcomes)
                                     for s in ("DATA_GAP", "OPEN_AT_END", "ENTRY_BAR_UNAVAILABLE")},
            "outcomes": outcomes,
        }
    return {
        "mode": "CONDITIONAL_ENTRY_EXIT_RESEARCH_ONLY",
        "strategy": "TREND_PULLBACK",
        "variants": variants,
        "warning": ("Each scenario independently reuses the baseline entry, so holdings may "
                    "overlap; conditional P&L is not a portfolio backtest or broker fill. "
                    "Intrabar paths unknown; stop-first; gap windows excluded; fixed "
                    "spread/slippage/commission assumptions. No live strategy changes."),
    }
