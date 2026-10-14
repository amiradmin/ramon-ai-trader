"""Read-only research: Donchian(20) breakout + EMA trend + ATR bracket.

Independent from production signals and EA. No historical data leakage:
signal uses only completed M15 bars and entry is next bar open.
All costs, spread and the intra-candle fill limitations are explicit.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Sequence
from ramon.core import Bar, atr14


@dataclass(frozen=True)
class Breakout:
    side: str
    stop_distance: float
    target_distance: float


def ema(values: Sequence[float], period: int) -> float:
    if len(values) < period:
        raise ValueError("insufficient EMA warmup")
    result = sum(values[:period]) / period
    alpha = 2.0 / (period + 1)
    for val in values[period:]:
        result += alpha * (val-result)
    return result


def signal(bars: Sequence[Bar], *, channel: int = 20, slow: int = 55,
           fast: int = 21, atr_stop: float = 1.5,
           reward_multiple: float = 2.0) -> Breakout | None:
    """Use only closed candles; prior channel excludes the trigger candle."""
    if len(bars) < max(slow+25, 128):
        return None
    close = [x.close for x in bars]
    efast, eslow = ema(close,fast), ema(close,slow)
    last = bars[-1]
    previous = bars[-channel-1:-1]
    high,low = max(x.high for x in previous),min(x.low for x in previous)
    atr = atr14(tuple(bars))
    if atr <= 0: return None
    distance=atr_stop*atr
    if last.close > high and efast > eslow and last.close > efast:
        return Breakout("BUY",distance,reward_multiple*distance)
    if last.close < low and efast < eslow and last.close < efast:
        return Breakout("SELL",distance,reward_multiple*distance)
    return None
