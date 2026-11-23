"""Research-only impulse continuation replay on closed M15 OHLC bars.

No broker/EA/command imports or live strategy registration. Signals on closed
bars; entry at the NEXT candle open. This is not proof of profitability.
"""
from datetime import datetime, timezone
from math import floor, isfinite

WARMUP = 60
ATR_PERIOD = 14
BREAKOUT_LOOKBACK = 12
MAX_HOLD = 12


def impulse_continuation_research(rows, *, start_ts, end_ts, balance,
                                  spread_points, point, tick_size, tick_value,
                                  volume_min, volume_max, volume_step,
                                  risk_percent=0.5, slippage_points=0,
                                  commission_per_lot=0):
    """Descriptive independent sequential position replay; no trading side effects."""
    result = {"setup": "IMPULSE_CONTINUATION", "mode": "RESEARCH_ONLY",
              "executions_authorized": False, "signals": 0, "trades": [],
              "rejections": {}, "open_position": False,
              "assumptions": {"entry": "NEXT_BAR_OPEN", "intrabar": "STOP_FIRST",
                              "max_hold_m15": MAX_HOLD, "atr_period": ATR_PERIOD,
                              "breakout_lookback": BREAKOUT_LOOKBACK,
                              "risk_percent": min(risk_percent, 0.5)}}
    def reject(reason):
        result["rejections"][reason] = result["rejections"].get(reason, 0) + 1
    if not rows or any(not isfinite(float(v)) or float(v) <= 0 for v in (
            balance, point, tick_size, tick_value, volume_min, volume_step)):
        result["status"] = "INVALID_INPUT"
        return result
    if volume_max < volume_min or not 0 < risk_percent <= 0.5:
        result["status"] = "INVALID_INPUT"
        return result
    spread = spread_points * point
    slip = slippage_points * point
    value_per_unit = tick_value / tick_size
    cash = float(balance)
    peak = cash
    max_drawdown = 0.0
    open_pos = None
    last_signal_index = -BREAKOUT_LOOKBACK
    last_segment_start = 0
    for i, bar in enumerate(rows):
        if i and bar["time"] - rows[i-1]["time"] != 900:
            # Never carry simulated risk across unknowable OHLC gaps.
            if open_pos:
                reject("DATA_GAP_POSITION_UNRESOLVED")
                result["status"] = "INCOMPLETE_DATA_GAP"
                result["open_position"] = True
                return result
            last_segment_start = i
            reject("DATA_GAP_RESET")
        if open_pos:
            side = open_pos["side"]
            if not start_ts <= bar["time"] < end_ts:
                result["open_position"] = True
                break
            if open_pos.get("enter_on") == i:
                entry = bar["open"] + (spread + slip if side == "BUY" else -slip)
                if not (open_pos["stop"] < entry < open_pos["target"] if side == "BUY"
                        else open_pos["target"] < entry < open_pos["stop"]):
                    reject("NEXT_OPEN_OUTSIDE_STOPS")
                    open_pos = None
                else:
                    stop_distance = abs(entry - open_pos["stop"])
                    reward_distance = abs(open_pos["target"] - entry)
                    if reward_distance / stop_distance < 1.2:
                        reject("NEXT_OPEN_REWARD_RISK_TOO_LOW")
                        open_pos = None
                        continue
                    risk_budget = cash * risk_percent / 100
                    risk_per_lot = stop_distance * value_per_unit
                    affordable = floor((risk_budget / risk_per_lot - volume_min) / volume_step + 1e-9)
                    volume = volume_min + max(0, affordable) * volume_step
                    volume = min(volume, volume_max)
                    if (risk_budget < volume_min * risk_per_lot or
                            volume * risk_per_lot > risk_budget + 1e-9):
                        reject("MINIMUM_LOT_EXCEEDS_RISK")
                        open_pos = None
                    else:
                        open_pos.update(entry=entry, volume=volume, opened_at=bar["time"])
            if open_pos and "entry" in open_pos:
                side = open_pos["side"]
                stop_hit = (bar["low"] <= open_pos["stop"] if side == "BUY"
                            else bar["high"] + spread >= open_pos["stop"])
                target_hit = (bar["high"] >= open_pos["target"] if side == "BUY"
                              else bar["low"] + spread <= open_pos["target"])
                timed = i - open_pos["enter_on"] >= MAX_HOLD
                if stop_hit or target_hit or timed:
                    # If the candle opens through the stop, assume the worse open.
                    stop_level = (min(bar["open"], open_pos["stop"]) if side == "BUY"
                                  else max(bar["open"]+spread, open_pos["stop"]))
                    level = stop_level if stop_hit else open_pos["target"] if target_hit else (
                        bar["close"] if side == "BUY" else bar["close"]+spread)
                    exit_price = level + (-slip if side == "BUY" else slip)
                    pnl = ((exit_price-open_pos["entry"]) *
                           (1 if side == "BUY" else -1) * value_per_unit *
                           open_pos["volume"] - commission_per_lot * open_pos["volume"])
                    cash += pnl
                    peak = max(peak, cash)
                    if peak > 0:
                        max_drawdown = max(max_drawdown, (peak-cash)/peak*100)
                    result["trades"].append({
                        "signal_at":open_pos["signal_at"],
                        "opened_at":open_pos["opened_at"],
                        "closed_at":bar["time"], "side":side,
                        "entry":round(open_pos["entry"],5),
                        "exit":round(exit_price,5),
                        "stop":round(open_pos["stop"],5),
                        "target":round(open_pos["target"],5),
                        "volume":round(open_pos["volume"],4),
                        "pnl":round(pnl,4),
                        "reason":"STOP" if stop_hit else "TARGET" if target_hit else "TIME"})
                    open_pos = None
            # Skip new entries on candle used for an exit or pending fill.
            continue
        if i < WARMUP or i - last_segment_start < WARMUP:
            continue
        if not start_ts <= bar["time"] < end_ts or i >= len(rows)-1:
            continue
        if rows[i+1]["time"] - bar["time"] != 900:
            reject("NEXT_BAR_GAP")
            continue
        history = rows[i-ATR_PERIOD:i+1]
        tr = [max(history[k]["high"]-history[k]["low"],
                  abs(history[k]["high"]-history[k-1]["close"]),
                  abs(history[k]["low"]-history[k-1]["close"]))
              for k in range(1,len(history))]
        atr = sum(tr)/ATR_PERIOD
        if not atr or not isfinite(atr):
            reject("ATR_INVALID")
            continue
        prior = rows[i-BREAKOUT_LOOKBACK:i]
        up = max(x["high"] for x in prior)
        down = min(x["low"] for x in prior)
        closes = [x["close"] for x in rows[i-50:i+1]]
        ema_fast = sum(closes[-20:])/20
        ema_slow = sum(closes[-50:])/50
        side = ("BUY" if bar["close"] > up and ema_fast > ema_slow
                else "SELL" if bar["close"] < down and ema_fast < ema_slow
                else None)
        if not side:
            reject("NO_CONFIRMED_TREND_BREAKOUT")
            continue
        candle_range = bar["high"]-bar["low"]
        body = abs(bar["close"]-bar["open"])
        if not 0.7*atr <= candle_range <= 2.2*atr or body < 0.5*candle_range:
            reject("IMPULSE_STRENGTH_OR_EXHAUSTION")
            continue
        if spread > 0.12*atr:
            reject("SPREAD_TOO_WIDE")
            continue
        if i - last_signal_index < BREAKOUT_LOOKBACK:
            reject("DUPLICATE_IMPULSE_WINDOW")
            continue
        # Stop at a structural reference, not an arbitrary tighter stop.
        stop = min(x["low"] for x in rows[i-5:i+1]) - 0.2*atr if side == "BUY" else (
               max(x["high"] for x in rows[i-5:i+1]) + 0.2*atr)
        expected_entry = bar["close"] + (spread if side == "BUY" else 0)
        distance = abs(expected_entry-stop)
        if distance < 0.6*atr or distance > 3*atr:
            reject("STRUCTURAL_STOP_DISTANCE")
            continue
        target = expected_entry + (1 if side == "BUY" else -1)*1.5*distance
        result["signals"] += 1
        last_signal_index = i
        open_pos = {"side":side,"stop":stop,"target":target,
                    "signal_at":bar["time"],"enter_on":i+1}
    trades = result["trades"]
    wins = [t for t in trades if t["pnl"]>0]
    losses = [t for t in trades if t["pnl"]<0]
    gross_profit = sum(t["pnl"] for t in wins)
    gross_loss = -sum(t["pnl"] for t in losses)
    result.update(status=result.get("status","DESCRIPTIVE_ONLY"),
                  closed_trades=len(trades),
                  wins=len(wins),
                  win_rate=round(100*len(wins)/len(trades),2) if trades else None,
                  net_gain=round(cash-balance,2),
                  profit_factor=round(gross_profit/gross_loss,3) if gross_loss else None,
                  max_drawdown_pct=round(max_drawdown,2),
                  open_position=bool(open_pos),
                  limitations=("Fixed modeled costs; OHLC stop-first; no historical ticks, "
                               "news, actual broker fills, open-position reconciliation or "
                               "live authorization. Replay results cannot establish profitability."))
    return result
