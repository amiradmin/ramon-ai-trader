"""Reconstructed read-only MA feature lab; NOT the lost original source.

Only completed chronological OHLC bars. No broker connections or order APIs.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from statistics import mean
from .core import atr14
from .history import load_bars

BASE = ("ret_1_atr", "ret_4_atr", "ema_fast_distance_atr",
        "ema_slow_distance_atr", "ema_gap_atr", "sma_distance_atr",
        "range_8_atr", "body_atr")
MA = ("ema_fast_distance_atr", "ema_slow_distance_atr",
      "ema_gap_atr", "sma_distance_atr")


def _ema(values, period):
    if period <= 0 or not values:
        raise ValueError("invalid EMA input")
    level = float(values[0])
    alpha = 2 / (period + 1)
    for close in values[1:]:
        level += alpha * (float(close) - level)
    return level


def features(bars):
    """Past-only numeric features; at least 64 completed M15 bars required."""
    if len(bars) < 64:
        raise ValueError("need 64 completed bars")
    window = bars[-64:]
    if any(b.time >= n.time or n.time - b.time != 900
           for b, n in zip(window, window[1:])):
        raise ValueError("non-contiguous or unsorted M15 bars")
    if any(not all(math.isfinite(v) for v in (b.open, b.high, b.low, b.close))
           or b.low > min(b.open, b.close)
           or b.high < max(b.open, b.close)
           for b in window):
        raise ValueError("invalid OHLC")
    atr = float(atr14(window))
    if not math.isfinite(atr) or atr <= 0:
        raise ValueError("invalid ATR")
    close = [b.close for b in window]
    fast = _ema(close, 8)
    slow = _ema(close, 21)
    sma = mean(close[-20:])
    last = close[-1]
    return {
        "ret_1_atr": (last - close[-2]) / atr,
        "ret_4_atr": (last - close[-5]) / atr,
        "ema_fast_distance_atr": (last - fast) / atr,
        "ema_slow_distance_atr": (last - slow) / atr,
        "ema_gap_atr": (fast - slow) / atr,
        "sma_distance_atr": (last - sma) / atr,
        "range_8_atr": (max(b.high for b in window[-8:]) -
                        min(b.low for b in window[-8:])) / atr,
        "body_atr": (window[-1].close - window[-1].open) / atr,
    }


def dataset(bars, horizon=1):
    """Samples carry feature cutoff and label completion times for purge."""
    if horizon < 1:
        raise ValueError("horizon must be positive")
    rows = []
    for i in range(63, len(bars) - horizon):
        try:
            row = features(bars[i - 63:i + 1])
        except ValueError:
            continue
        change = bars[i + horizon].close - bars[i].close
        rows.append({"time": bars[i].time, "label_time": bars[i + horizon].time,
                     "features": row, "label": int(change > 0)})
    return rows


def evaluate(rows, folds=3):
    """Purged forward-chaining majority-class baseline (no look-ahead)."""
    rows = sorted(rows, key=lambda r: r["time"])
    if folds < 1 or len(rows) < (folds + 1) * 5:
        raise ValueError("not enough samples")
    step = len(rows) // (folds + 1)
    results = []
    for k in range(1, folds + 1):
        boundary = k * step
        test = rows[boundary:min(boundary + step, len(rows))]
        if not test:
            continue
        train = [r for r in rows[:boundary] if r["label_time"] < test[0]["time"]]
        if not train:
            raise ValueError("purged training window empty")
        majority = int(sum(r["label"] for r in train) * 2 >= len(train))
        accuracy = sum(r["label"] == majority for r in test) / len(test)
        results.append({"train": len(train), "test": len(test),
                        "accuracy": accuracy, "prediction": majority})
    return {"model": "majority_baseline", "folds": results, "live_enabled": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--symbol", default="XAUUSD_l")
    parser.add_argument("--horizon", type=int, default=1)
    parser.add_argument("--folds", type=int, default=3)
    args = parser.parse_args()
    bars, _ = load_bars(args.db, args.symbol)
    print(json.dumps(evaluate(dataset(bars, args.horizon), args.folds), indent=2))


if __name__ == "__main__":
    main()
