"""Reconstructed chronological candle-direction research primitives.

This is a NEW conservative replacement based on recovered function names,
not a decompilation of the original model. No orders, no production signal.
"""
from __future__ import annotations

import argparse
from bisect import bisect_right
from dataclasses import dataclass
import json
import math
from pathlib import Path
from .core import atr14
from .history import load_bars
from .moving_average_lab import features as ma_features

CLASSES = ("DOWN", "FLAT", "UP")


@dataclass(frozen=True)
class Sample:
    time: int
    label_time: int
    features: dict[str, float]
    label: str


def aggregate_h1(bars):
    """Only groups of four contiguous completed M15 bars become H1."""
    output = []
    for i in range(0, len(bars) - 3, 4):
        group = bars[i:i + 4]
        if any(b.time + 900 != n.time for b, n in zip(group, group[1:])):
            continue
        from .core import Bar
        output.append(Bar(group[0].time, group[0].open,
                          max(b.high for b in group), min(b.low for b in group),
                          group[-1].close))
    return output


def closed_context(bars, timestamp, count, seconds):
    """timestamp is the *exclusive* right edge of the completed candle."""
    if count < 1 or seconds < 1:
        raise ValueError("invalid context")
    ends = [b.time + seconds for b in bars]
    right = bisect_right(ends, timestamp)
    context = bars[max(0, right - count):right]
    if len(context) != count:
        raise ValueError("insufficient closed context")
    if any(a.time + seconds != b.time for a, b in zip(context, context[1:])):
        raise ValueError("gapped context")
    return context


def candle_features(context, scale, prefix="m15"):
    if not context or not math.isfinite(scale) or scale <= 0:
        raise ValueError("invalid feature context")
    b = context[-1]
    return {f"{prefix}_body": (b.close - b.open) / scale,
            f"{prefix}_range": (b.high - b.low) / scale,
            f"{prefix}_upper_wick": (b.high - max(b.open, b.close)) / scale,
            f"{prefix}_lower_wick": (min(b.open, b.close) - b.low) / scale}


def build_samples(bars, spreads=None, micro=None, horizon=1, point=.01,
                  fallback=42, friction=.1):
    """Labels use future closes; features never do. Observed spread or fallback."""
    if horizon < 1 or point <= 0 or fallback < 0 or friction < 0:
        raise ValueError("invalid benchmark parameters")
    spreads = spreads if spreads is not None else [fallback] * len(bars)
    if len(spreads) < len(bars):
        raise ValueError("missing spreads")
    output = []
    for i in range(63, len(bars) - horizon):
        window = bars[i - 63:i + 1]
        if bars[i + horizon].time - bars[i].time != horizon * 900:
            continue
        try:
            base = ma_features(window)
            scale = atr14(window)
            base.update(candle_features(window, scale))
        except ValueError:
            continue
        cost = (spreads[i] if spreads[i] > 0 else fallback) * point + friction
        delta = bars[i + horizon].close - bars[i].close
        label = "UP" if delta > cost else "DOWN" if delta < -cost else "FLAT"
        output.append(Sample(bars[i].time, bars[i + horizon].time, base, label))
    return output


def split(rows, train_ratio=.6, valid_ratio=.2):
    """Chronological split with purge of overlapping future labels."""
    if not 0 < train_ratio < 1 or not 0 < valid_ratio < 1 - train_ratio:
        raise ValueError("invalid ratios")
    rows = sorted(rows, key=lambda r: r.time)
    if len(rows) < 15:
        raise ValueError("insufficient samples")
    first, second = int(len(rows) * train_ratio), int(len(rows) * (train_ratio + valid_ratio))
    valid = rows[first:second]
    test = rows[second:]
    train = [r for r in rows[:first] if r.label_time < valid[0].time]
    valid = [r for r in valid if r.label_time < test[0].time]
    if not train or not valid or not test:
        raise ValueError("purging emptied a split")
    return train, valid, test


def temper(probabilities, temperature=1.0):
    if temperature <= 0:
        raise ValueError("temperature must be positive")
    values = [float(p) for p in probabilities]
    if len(values) != len(CLASSES) or any(not math.isfinite(v) or v < 0 for v in values):
        raise ValueError("invalid probability vector")
    if sum(values) <= 0:
        raise ValueError("zero probability")
    weights = [max(p, 1e-12) ** (1 / temperature) for p in values]
    return [p / sum(weights) for p in weights]


def metrics(probabilities, rows):
    if len(probabilities) != len(rows) or not rows:
        raise ValueError("sample/probability mismatch")
    correct = 0
    for probs, sample in zip(probabilities, rows):
        normalized = temper(probs)
        guess = CLASSES[max(range(3), key=lambda j: normalized[j])]
        correct += guess == sample.label
    return {"accuracy": correct / len(rows), "samples": len(rows)}


def replay(probabilities, rows, bars=None, spreads=None, horizon=1,
           threshold=.6, point=.01, fallback=42, friction=.1):
    """Decision-count diagnostic only. No simulated PnL without bid/ask ticks."""
    if not .5 <= threshold <= 1:
        raise ValueError("invalid threshold")
    decisions = []
    for raw, sample in zip(probabilities, rows):
        p = temper(raw)
        best = max(range(3), key=lambda i: p[i])
        action = CLASSES[best] if p[best] >= threshold else "WAIT"
        decisions.append({"time": sample.time, "direction": action})
    return {"decisions": decisions, "profit_claim": False}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db", required=True)
    p.add_argument("--symbol", default="XAUUSD_l")
    p.add_argument("--horizon", type=int, default=1)
    args = p.parse_args()
    bars, spreads = load_bars(args.db, args.symbol)
    rows = build_samples(bars, spreads, horizon=args.horizon)
    train, valid, test = split(rows)
    print(json.dumps({"train": len(train), "valid": len(valid), "test": len(test),
                      "live_enabled": False}, indent=2))


if __name__ == "__main__":
    main()
