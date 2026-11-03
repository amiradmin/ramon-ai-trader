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
        if not indices or times[-1] < end_ts:
            continue
        after = [i for i, ts in enumerate(times) if ts >= end_ts]
        if not after or after[-1] != len(times) - 1:
            continue
        top, bottom = max(highs[i] for i in indices), min(lows[i] for i in indices)
        buffer = float(settings["breakout_buffer_atr"]) * atr
        if closes[-1] > top + buffer:
            return {"type": "SESSION_OPENING_RANGE_BREAKOUT", "side": "BUY",
                    "session": label, "range_high": top, "range_low": bottom,
                    "opening_range_start": start_ts, "opening_range_end": end_ts}
        if closes[-1] < bottom - buffer:
            return {"type": "SESSION_OPENING_RANGE_BREAKOUT", "side": "SELL",
                    "session": label, "range_high": top, "range_low": bottom,
                    "opening_range_start": start_ts, "opening_range_end": end_ts}
    return None


def _failed_breakout_sweep(highs, lows, closes, atr, t, cfg):
    lookback = int(cfg["lookback_bars"])
    if t < lookback:
        return None
    prior_high, prior_low = max(highs[t-lookback:t]), min(lows[t-lookback:t])
    buffer = float(cfg["sweep_buffer_atr"]) * atr
    if highs[t] > prior_high + buffer and closes[t] < prior_high:
        return {"type": "FAILED_BREAKOUT_SWEEP", "side": "SELL", "level": prior_high,
                "sweep_extreme": highs[t], "invalidation_price": highs[t],
                "score": 0.60 + min(0.20, (highs[t] - prior_high) / atr * 0.10)}
    if lows[t] < prior_low - buffer and closes[t] > prior_low:
        return {"type": "FAILED_BREAKOUT_SWEEP", "side": "BUY", "level": prior_low,
                "sweep_extreme": lows[t], "invalidation_price": lows[t],
                "score": 0.60 + min(0.20, (prior_low - lows[t]) / atr * 0.10)}
    return None


def _range_edge_fade(opens, highs, lows, closes, atr, t, regime_label, cfg):
    # Permitted only in a confirmed ranging regime.
    lookback = int(cfg["lookback_bars"])
    if regime_label != "RANGING" or t < lookback:
        return None
    top, bottom = max(highs[t-lookback:t]), min(lows[t-lookback:t])
    span = max(highs[t] - lows[t], 1e-12)
    upper_wick = highs[t] - max(opens[t], closes[t])
    lower_wick = min(opens[t], closes[t]) - lows[t]
    ratio = float(cfg["minimum_rejection_wick_ratio"])
    edge = float(cfg["edge_buffer_atr"]) * atr
    if highs[t] >= top - edge and closes[t] < top and upper_wick / span >= ratio:
        return {"type": "RANGE_EDGE_FADE", "side": "SELL", "range_high": top, "range_low": bottom,
                "invalidation_price": highs[t], "score": 0.55 + 0.30 * min(1.0, upper_wick / span)}
    if lows[t] <= bottom + edge and closes[t] > bottom and lower_wick / span >= ratio:
        return {"type": "RANGE_EDGE_FADE", "side": "BUY", "range_high": top, "range_low": bottom,
                "invalidation_price": lows[t], "score": 0.55 + 0.30 * min(1.0, lower_wick / span)}
    return None


def _session_breakout(times, highs, lows, closes, atr, cfg):
    candidate = _session_open_range(times, highs, lows, closes, atr, cfg)
    if not candidate:
        return None
    buy = candidate["side"] == "BUY"
    candidate["invalidation_price"] = candidate["range_low"] if buy else candidate["range_high"]
    extension = abs(closes[-1] - (candidate["range_high"] if buy else candidate["range_low"])) / atr
    candidate["score"] = 0.55 + min(0.25, extension * 0.10)
    return candidate


def _compression_expansion(opens, highs, lows, closes, atr, t, cfg):
    """A directional candle that breaks out of a genuinely tight preceding range.

    The compression window is the ``lookback`` completed bars immediately before the
    expansion candle. Its *total* high-low width must fit in ``compression_max_width_atr``
    ATR (the old formula multiplied the limit by the lookback, so almost any range passed),
    and the expansion candle must also close beyond that range in its own direction.
    """
    lookback = int(cfg["compression_lookback_bars"])
    if t < lookback:
        return None
    prior_high, prior_low = max(highs[t-lookback:t]), min(lows[t-lookback:t])
    prior_width = prior_high - prior_low
    if prior_width > float(cfg["compression_max_width_atr"]) * atr:
        return None
    body = abs(closes[t] - opens[t])
    if body < float(cfg["expansion_body_atr"]) * atr:
        return None
    score = 0.55 + min(0.25, body / atr * 0.10)
    if closes[t] > opens[t] and closes[t] > prior_high:
        return {"type": "VOLATILITY_COMPRESSION_EXPANSION", "side": "BUY",
                "compression_range": prior_width, "compression_high": prior_high,
                "compression_low": prior_low, "invalidation_price": lows[t], "score": score}
    if closes[t] < opens[t] and closes[t] < prior_low:
        return {"type": "VOLATILITY_COMPRESSION_EXPANSION", "side": "SELL",
                "compression_range": prior_width, "compression_high": prior_high,
                "compression_low": prior_low, "invalidation_price": highs[t], "score": score}
    return None


def _exhaustion_reversal(highs, lows, closes, rsi_values, atr, t, swing_highs, swing_lows, cfg):
    # Last-resort setup: extended move + RSI extreme + confirmed swing CHoCH.
    lookback = int(cfg["extension_lookback_bars"])
    if t < lookback or not math.isfinite(rsi_values[t]):
        return None
    extension = closes[t] - closes[t-lookback]
    threshold = float(cfg["minimum_extension_atr"]) * atr
    extreme = float(cfg["rsi_extreme"])
    if extension >= threshold and rsi_values[t] >= extreme:
        levels = [i for i in swing_lows if i < t]
        if levels and closes[t] < lows[levels[-1]]:
            return {"type": "EXHAUSTION_REVERSAL", "side": "SELL", "extension_atr": extension / atr,
                    "choch_level": lows[levels[-1]], "invalidation_price": highs[t],
                    "score": 0.55 + min(0.25, abs(extension) / atr * 0.05)}
    if extension <= -threshold and rsi_values[t] <= 100.0 - extreme:
        levels = [i for i in swing_highs if i < t]
        if levels and closes[t] > highs[levels[-1]]:
            return {"type": "EXHAUSTION_REVERSAL", "side": "BUY", "extension_atr": abs(extension) / atr,
                    "choch_level": highs[levels[-1]], "invalidation_price": lows[t],
                    "score": 0.55 + min(0.25, abs(extension) / atr * 0.05)}
    return None


def detect_all_additional_opportunities(times: Sequence[float], opens: Sequence[float],
        highs: Sequence[float], lows: Sequence[float], closes: Sequence[float],
        atr_values: Sequence[float], adx_values: Sequence[float], rsi_values: Sequence[float],
        swing_highs: Sequence[int], swing_lows: Sequence[int], regime_label: str,
        setups: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Return every enabled supplemental candidate on the latest completed bar.

    All windows exclude future bars. Nothing here picks entries, stops, targets or size.
    """
    n = len(closes)
    if n < 3:
        return []
    t = n - 1
    atr = atr_values[t]
    if not math.isfinite(atr) or atr <= 0:
        return []
    found: list[dict[str, Any]] = []

    def add(candidate: dict[str, Any] | None) -> None:
        if candidate is not None:
            found.append(candidate)

    if setups["failed_breakout_sweep"]["enabled"]:
        add(_failed_breakout_sweep(highs, lows, closes, atr, t, setups["failed_breakout_sweep"]))
    if setups["range_edge_fade"]["enabled"]:
        add(_range_edge_fade(opens, highs, lows, closes, atr, t, regime_label, setups["range_edge_fade"]))
    if setups["session_opening_range_breakout"]["enabled"]:
        add(_session_breakout(times, highs, lows, closes, atr, setups["session_opening_range_breakout"]))
    if setups["volatility_compression_expansion"]["enabled"]:
        add(_compression_expansion(opens, highs, lows, closes, atr, t, setups["volatility_compression_expansion"]))
    if setups["exhaustion_reversal"]["enabled"]:
        add(_exhaustion_reversal(highs, lows, closes, rsi_values, atr, t, swing_highs, swing_lows,
                                 setups["exhaustion_reversal"]))
    return found


def detect_additional_opportunity(times: Sequence[float], opens: Sequence[float],
        highs: Sequence[float], lows: Sequence[float], closes: Sequence[float],
        atr_values: Sequence[float], adx_values: Sequence[float], rsi_values: Sequence[float],
        swing_highs: Sequence[int], swing_lows: Sequence[int], regime_label: str,
        setups: Mapping[str, Any]) -> dict[str, Any] | None:
    """Return the highest-scoring enabled candidate (ties keep the documented setup order).

    Every enabled setup is evaluated, so an earlier setup can no longer hide a later one.
    The other setups that fired on the same bar are listed in ``also_detected``.
    """
    found = detect_all_additional_opportunities(
        times, opens, highs, lows, closes, atr_values, adx_values, rsi_values,
        swing_highs, swing_lows, regime_label, setups)
    if not found:
        return None
    best = max(found, key=lambda c: float(c.get("score", 0.0)))  # max() keeps the first of equal scores
    others = [c["type"] for c in found if c is not best]
    if others:
        best["also_detected"] = others
    return best
