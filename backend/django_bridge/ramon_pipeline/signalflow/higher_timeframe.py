"""Causal higher-timeframe bias built only from completed lower-timeframe candles.

Completed M15 candles are grouped into *complete* hourly buckets (the still-forming hour
and any hour with a missing candle are dropped), so no future information is used and no
extra data feed is required.
"""
from __future__ import annotations

import math
from typing import Sequence

BULLISH = "BULLISH"
BEARISH = "BEARISH"
NEUTRAL = "NEUTRAL"
UNAVAILABLE = "UNAVAILABLE"


def _ema(values: Sequence[float], period: int) -> list[float]:
    alpha = 2.0 / (period + 1.0)
    out = [values[0]]
    for value in values[1:]:
        out.append(alpha * value + (1.0 - alpha) * out[-1])
    return out


def higher_timeframe_bias(times: Sequence[float], closes: Sequence[float], interval_seconds: float,
                          *, bucket_minutes: int = 60, ema_period: int = 20,
                          slope_bars: int = 3) -> str:
    """BULLISH / BEARISH / NEUTRAL, or UNAVAILABLE when there is not enough clean history."""
    if len(times) != len(closes) or interval_seconds <= 0:
        return UNAVAILABLE
    bucket_seconds = bucket_minutes * 60
    per_bucket = int(round(bucket_seconds / interval_seconds))
    if per_bucket < 2 or abs(per_bucket * interval_seconds - bucket_seconds) > 1e-6:
        return UNAVAILABLE
    buckets: dict[int, list[float]] = {}
    for stamp, close in zip(times, closes):
        if not math.isfinite(close):
            return UNAVAILABLE
        buckets.setdefault(int(stamp // bucket_seconds), []).append(close)
    complete = [(key, vals[-1]) for key, vals in sorted(buckets.items()) if len(vals) == per_bucket]
    if len(complete) < ema_period + slope_bars:
        return UNAVAILABLE
    htf_closes = [close for _, close in complete]
    ema = _ema(htf_closes, ema_period)
    rising = ema[-1] > ema[-1 - slope_bars]
    falling = ema[-1] < ema[-1 - slope_bars]
    if htf_closes[-1] > ema[-1] and rising:
        return BULLISH
    if htf_closes[-1] < ema[-1] and falling:
        return BEARISH
    return NEUTRAL
