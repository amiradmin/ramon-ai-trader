"""Causal, opt-in supplemental XAUUSD M15 opportunity detectors.

The functions consume only completed OHLC bars and return a candidate description.
They do not calculate entries, stops, targets, position size, or authorize execution.
"""
from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo
from typing import Any, Mapping, Sequence
import math


def _session_open_range(times: Sequence[float], highs: Sequence[float], lows: Sequence[float],
                         closes: Sequence[float], atr: float, settings: Mapping[str, Any]) -> dict[str, Any] | None:
    sessions = settings.get("sessions", {})
    if not sessions:
        return None
    now = datetime.fromtimestamp(times[-1], tz=ZoneInfo("UTC"))
    for label, session in sessions.items():
        zone = ZoneInfo(session["timezone"])
        local = now.astimezone(zone)
        hour, minute = (int(x) for x in session["open"].split(":"))
        opened = datetime.combine(local.date(), time(hour, minute), tzinfo=zone)
        start_ts = opened.timestamp()
        end_ts = start_ts + int(settings["opening_range_minutes"]) * 60
        # The opening range must be fully completed before a later bar can break it.
        indices = [i for i, ts in enumerate(times[:-1]) if start_ts <= ts < end_ts]
        expected_bars = int(settings["opening_range_minutes"]) // 15
        if (len(indices) != expected_bars or times[indices[0]] != start_ts
                or any(times[b] - times[a] != 900 for a, b in zip(indices, indices[1:]))
                or times[-1] < end_ts):
            continue
        after = [i for i, ts in enumerate(times) if ts >= end_ts]
        if not after or after[-1] != len(times) - 1:
            continue
        top, bottom = max(highs[i] for i in indices), min(lows[i] for i in indices)
        buffer = float(settings["breakout_buffer_atr"]) * atr
        if closes[-1] > top + buffer and closes[-2] <= top + buffer:
            return {"type": "SESSION_OPENING_RANGE_BREAKOUT", "side": "BUY",
                    "session": label, "range_high": top, "range_low": bottom,
                    "opening_range_start": start_ts, "opening_range_end": end_ts}
        if closes[-1] < bottom - buffer and closes[-2] >= bottom - buffer:
            return {"type": "SESSION_OPENING_RANGE_BREAKOUT", "side": "SELL",
                    "session": label, "range_high": top, "range_low": bottom,
                    "opening_range_start": start_ts, "opening_range_end": end_ts}
    return None


def detect_additional_opportunity(times: Sequence[float], opens: Sequence[float],
        highs: Sequence[float], lows: Sequence[float], closes: Sequence[float],
        atr_values: Sequence[float], adx_values: Sequence[float], rsi_values: Sequence[float],
        swing_highs: Sequence[int], swing_lows: Sequence[int], regime_label: str,
        setups: Mapping[str, Any]) -> dict[str, Any] | None:
    """Return the first enabled supplemental candidate; all windows exclude future bars."""
    n = len(closes)
    if n < 3:
        return None
    t = n - 1
    atr = atr_values[t]
    if not math.isfinite(atr) or atr <= 0:
        return None

    # 1. Failed breakout / liquidity sweep: breach a prior confirmed range and close back inside.
    cfg = setups["failed_breakout_sweep"]
    if cfg["enabled"]:
        lookback = int(cfg["lookback_bars"])
        if t >= lookback:
            prior_high, prior_low = max(highs[t-lookback:t]), min(lows[t-lookback:t])
            buffer = float(cfg["sweep_buffer_atr"]) * atr
            if highs[t] > prior_high + buffer and closes[t] < prior_high:
                return {"type": "FAILED_BREAKOUT_SWEEP", "side": "SELL", "level": prior_high,
                        "sweep_extreme": highs[t], "invalidation_price": highs[t], "score": 0.60 + min(0.20, (highs[t] - prior_high) / atr * 0.10)}
            if lows[t] < prior_low - buffer and closes[t] > prior_low:
                return {"type": "FAILED_BREAKOUT_SWEEP", "side": "BUY", "level": prior_low,
                        "sweep_extreme": lows[t], "invalidation_price": lows[t], "score": 0.60 + min(0.20, (prior_low - lows[t]) / atr * 0.10)}

    # 2. Range-edge fade: require a developed, two-sided range and a decisive
    # rejection back inside it. A low ADX reading alone is not enough evidence.
    cfg = setups["range_edge_fade"]
    if cfg["enabled"] and regime_label == "RANGING":
        lookback = int(cfg["lookback_bars"])
        if t >= lookback:
            prior_highs, prior_lows = highs[t-lookback:t], lows[t-lookback:t]
            top, bottom = max(prior_highs), min(prior_lows)
            width = top - bottom
            width_atr = width / atr
            edge = float(cfg["edge_buffer_atr"]) * atr
            min_width = float(cfg["minimum_range_width_atr"])
            max_width = float(cfg["maximum_range_width_atr"])
            touch_tolerance = float(cfg["boundary_touch_tolerance_atr"]) * atr
            min_touches = int(cfg["minimum_boundary_touches"])
            # Count distinct prior candle reactions near each boundary. The current
            # signal candle is excluded from boundary construction and touch counts.
            upper_touches = sum(1 for h in prior_highs if h >= top - touch_tolerance)
            lower_touches = sum(1 for lo in prior_lows if lo <= bottom + touch_tolerance)
            span = max(highs[t] - lows[t], 1e-12)
            upper_wick = highs[t] - max(opens[t], closes[t])
            lower_wick = min(opens[t], closes[t]) - lows[t]
            upper_ratio, lower_ratio = upper_wick / span, lower_wick / span
            close_location = (closes[t] - lows[t]) / span
            min_wick = float(cfg["minimum_rejection_wick_ratio"])
            min_reentry = float(cfg["minimum_close_reentry_atr"]) * atr
            valid_range = min_width <= width_atr <= max_width
            tested_both_sides = upper_touches >= min_touches and lower_touches >= min_touches

            if valid_range and tested_both_sides:
                # SELL: price probes the upper boundary, rejects it, and closes
                # materially back inside the range in the lower part of the candle.
                upper_probe = highs[t] >= top - edge
                upper_reentry = top - closes[t] >= min_reentry
                if (upper_probe and upper_reentry and upper_ratio >= min_wick
                        and close_location <= float(cfg["maximum_sell_close_location"])):
                    score = 0.55 + 0.15 * min(1.0, upper_ratio) + 0.10 * min(1.0, (top - closes[t]) / max(edge, atr * 0.01))
                    return {"type": "RANGE_EDGE_FADE", "side": "SELL", "range_high": top,
                            "range_low": bottom, "range_width_atr": round(width_atr, 3),
                            "boundary_touches": {"upper": upper_touches, "lower": lower_touches},
                            "close_location": round(close_location, 3),
                            "invalidation_price": highs[t], "score": min(0.90, score)}
                # BUY: symmetric lower-boundary rejection, closing in the upper
                # part of the signal candle and back inside the established range.
                lower_probe = lows[t] <= bottom + edge
                lower_reentry = closes[t] - bottom >= min_reentry
                if (lower_probe and lower_reentry and lower_ratio >= min_wick
                        and close_location >= float(cfg["minimum_buy_close_location"])):
                    score = 0.55 + 0.15 * min(1.0, lower_ratio) + 0.10 * min(1.0, (closes[t] - bottom) / max(edge, atr * 0.01))
                    return {"type": "RANGE_EDGE_FADE", "side": "BUY", "range_high": top,
                            "range_low": bottom, "range_width_atr": round(width_atr, 3),
                            "boundary_touches": {"upper": upper_touches, "lower": lower_touches},
                            "close_location": round(close_location, 3),
                            "invalidation_price": lows[t], "score": min(0.90, score)}

    # 3. Session opening range breakout, using IANA zones so DST is handled.
    cfg = setups["session_opening_range_breakout"]
    if cfg["enabled"]:
        candidate = _session_open_range(times, highs, lows, closes, atr, cfg)
        if candidate:
            candidate["invalidation_price"] = candidate["range_low"] if candidate["side"] == "BUY" else candidate["range_high"]
            extension = abs(closes[-1] - (candidate["range_high"] if candidate["side"] == "BUY" else candidate["range_low"])) / atr
            candidate["score"] = 0.55 + min(0.25, extension * 0.10)
            return candidate

    # 4. Compression followed by a directional expansion candle.
    cfg = setups["volatility_compression_expansion"]
    if cfg["enabled"]:
        lookback = int(cfg["compression_lookback_bars"])
        if t >= lookback + 1:
            prior_top, prior_bottom = max(highs[t-lookback:t]), min(lows[t-lookback:t])
            prior_width = prior_top - prior_bottom
            # Normalize by sqrt(time), not linearly by the bar count: the old
            # threshold accepted broad ranges as compression. Exclude the trigger.
            compressed = prior_width <= float(cfg["compression_max_atr"]) * atr * math.sqrt(lookback)
            body = abs(closes[t] - opens[t])
            if compressed and body >= float(cfg["expansion_body_atr"]) * atr:
                side = "BUY" if closes[t] > opens[t] else "SELL" if closes[t] < opens[t] else None
                outside_range = closes[t] > prior_top if side == "BUY" else closes[t] < prior_bottom
                if side and outside_range:
                    return {"type": "VOLATILITY_COMPRESSION_EXPANSION", "side": side,
                            "compression_range": prior_width,
                            "invalidation_price": lows[t] if side == "BUY" else highs[t], "score": 0.55 + min(0.25, body / atr * 0.10)}

    # 5. Exhaustion reversal is last-resort and requires a confirmed swing CHoCH.
    cfg = setups["exhaustion_reversal"]
    if cfg["enabled"]:
        lookback = int(cfg["extension_lookback_bars"])
        if t >= lookback and math.isfinite(rsi_values[t]):
            extension = closes[t] - closes[t-lookback]
            threshold = float(cfg["minimum_extension_atr"]) * atr
            extreme = float(cfg["rsi_extreme"])
            if extension >= threshold and rsi_values[t] >= extreme:
                levels = [i for i in swing_lows if i < t]
                if levels and closes[t] < lows[levels[-1]]:
                    return {"type": "EXHAUSTION_REVERSAL", "side": "SELL",
                            "extension_atr": extension / atr, "choch_level": lows[levels[-1]],
                            "invalidation_price": highs[t], "score": 0.55 + min(0.25, abs(extension) / atr * 0.05)}
            if extension <= -threshold and rsi_values[t] <= 100.0 - extreme:
                levels = [i for i in swing_highs if i < t]
                if levels and closes[t] > highs[levels[-1]]:
                    return {"type": "EXHAUSTION_REVERSAL", "side": "BUY",
                            "extension_atr": abs(extension) / atr, "choch_level": highs[levels[-1]],
                            "invalidation_price": lows[t], "score": 0.55 + min(0.25, abs(extension) / atr * 0.05)}
    return None
