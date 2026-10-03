"""Native research features inspired by published XAUUSD SMC/technical models.

These are deterministic features computed from Ramon bars.  They do not load
third-party model artifacts and they do not participate in live decisions.
"""
from __future__ import annotations

from math import sqrt
from statistics import mean, pstdev
from typing import Sequence

from .core import Bar


def _ema(values: Sequence[float], period: int) -> float:
    if not values:
        raise ValueError("values required")
    alpha = 2.0 / (period + 1.0)
    value = float(values[0])
    for x in values[1:]:
        value = alpha * float(x) + (1.0 - alpha) * value
    return value


def _rsi(values: Sequence[float], period: int = 14) -> float:
    if len(values) < period + 1:
        raise ValueError("insufficient closes for RSI")
    gains = []
    losses = []
    for a, b in zip(values[-period-1:-1], values[-period:]):
        d = b - a
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    avg_gain = mean(gains)
    avg_loss = mean(losses)
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def _ema_series(values: Sequence[float], period: int) -> list[float]:
    if not values:
        return []
    alpha = 2.0 / (period + 1.0)
    out = [float(values[0])]
    for x in values[1:]:
        out.append(alpha * float(x) + (1.0 - alpha) * out[-1])
    return out


def smc_technical_features(bars: Sequence[Bar]) -> dict[str, float]:
    """Return a local M15 feature vector compatible with Ramon research.

    Feature names intentionally mirror the public SMC-v2 feature vocabulary
    where practical, but formulas are implemented locally and independently.
    """
    if len(bars) < 60:
        raise ValueError("need at least 60 completed bars")
    closes = [b.close for b in bars]
    highs = [b.high for b in bars]
    lows = [b.low for b in bars]
    last = bars[-1]

    sma20 = mean(closes[-20:])
    sma50 = mean(closes[-50:])
    ema12 = _ema(closes[-60:], 12)
    ema26 = _ema(closes[-60:], 26)

    ema12_series = _ema_series(closes[-60:], 12)
    ema26_series = _ema_series(closes[-60:], 26)
    macd_series = [a - b for a, b in zip(ema12_series, ema26_series)]
    macd = macd_series[-1]
    macd_signal = _ema(macd_series, 9)
    macd_hist = macd - macd_signal

    std20 = pstdev(closes[-20:]) if len(closes[-20:]) > 1 else 0.0
    bb_middle = sma20
    bb_upper = sma20 + 2.0 * std20
    bb_lower = sma20 - 2.0 * std20

    # Three-candle fair value gap, using only completed bars.
    a, _, c = bars[-3], bars[-2], bars[-1]
    fvg_type = 0.0
    fvg_size = 0.0
    if c.low > a.high:
        fvg_type = 1.0
        fvg_size = c.low - a.high
    elif c.high < a.low:
        fvg_type = -1.0
        fvg_size = a.low - c.high

    # Price-only proxy for a directional order block.  The public source used
    # volume; Kaggle/Ramon external history may not have trustworthy volume, so
    # keep this explicit proxy separate from claims of equivalence.
    recent = bars[-20:]
    ranges = [max(b.high - b.low, 1e-12) for b in recent]
    bodies = [abs(b.close - b.open) for b in recent]
    threshold = mean(ranges)
    ob_type = 0.0
    if ranges[-1] >= threshold and bodies[-1] / ranges[-1] >= 0.7:
        ob_type = 1.0 if last.close > last.open else -1.0

    # Simple recovery/rejection proxy: long wick with close back inside prior bar.
    prev = bars[-2]
    upper_wick = last.high - max(last.open, last.close)
    lower_wick = min(last.open, last.close) - last.low
    recovery_type = 0.0
    if last.close < prev.high and upper_wick > abs(last.close - last.open):
        recovery_type = -1.0
    elif last.close > prev.low and lower_wick > abs(last.close - last.open):
        recovery_type = 1.0

    return {
        "Open": last.open,
        "High": last.high,
        "Low": last.low,
        "Close": last.close,
        "SMA_20": sma20,
        "SMA_50": sma50,
        "EMA_12": ema12,
        "EMA_26": ema26,
        "RSI": _rsi(closes),
        "MACD": macd,
        "MACD_signal": macd_signal,
        "MACD_hist": macd_hist,
        "BB_upper": bb_upper,
        "BB_middle": bb_middle,
        "BB_lower": bb_lower,
        "FVG_Size": fvg_size,
        "FVG_Type": fvg_type,
        "OB_Type": ob_type,
        "Recovery_Type": recovery_type,
        "Close_lag1": closes[-2],
        "Close_lag2": closes[-3],
        "Close_lag3": closes[-4],
    }
