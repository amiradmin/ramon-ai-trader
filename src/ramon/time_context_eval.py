"""Compare univariate and hour-aware Chronos-2 forecasts on the same M15 bars.

Research only: no MT5 settings, decisions or live orders are changed.
"""
from __future__ import annotations

import argparse
import json
from math import isfinite
from pathlib import Path

from .core import Bar, atr14
from .history import load_bars
from .model import ChronosForecaster, model_name
from .time_context import M15_SECONDS, tehran_hour


def windows(bars: tuple[Bar, ...], *, context: int, horizon: int,
            stride: int, max_windows: int) -> list[int]:
    if context < 128 or horizon < 1 or stride < 1 or max_windows < 1:
        raise ValueError("invalid evaluation settings")
    indices = [index for index in range(context - 1, len(bars) - horizon, stride)
               if all(bars[index + j].time - bars[index + j - 1].time == M15_SECONDS
                      for j in range(1, horizon + 1))]
    return indices[-max_windows:]


def summary(rows: list[dict]) -> dict:
    if not rows:
        return {"windows": 0}
    baseline = sum(row["baseline_error_atr"] for row in rows) / len(rows)
    clock = sum(row["hour_error_atr"] for row in rows) / len(rows)
    return {
        "windows": len(rows),
        "baseline_mae_atr": round(baseline, 5),
        "hour_mae_atr": round(clock, 5),
        "difference_mae_atr": round(clock - baseline, 5),
        "hour_better_windows": sum(row["hour_error_atr"] < row["baseline_error_atr"]
                                   for row in rows),
    }


def compare(bars: tuple[Bar, ...], model: ChronosForecaster, *, context: int = 256,
            horizon: int = 4, stride: int = 4, max_windows: int = 48) -> dict:
    candidates = windows(bars, context=context, horizon=horizon,
                         stride=stride, max_windows=max_windows)
    rows: list[dict] = []
    for index in candidates:
        history = bars[index - context + 1:index + 1]
        closes = [bar.close for bar in history]
        times = [bar.time for bar in history]
        baseline = model.forecast(closes, horizon)
        hour_aware = model.forecast_with_hour(closes, times, horizon)
        actual = bars[index + horizon].close
        atr = atr14(history)
        if not all(isfinite(value) and value > 0 for value in
                   (baseline.median, hour_aware.median, actual, atr)):
            raise ValueError("nonfinite model output or invalid ATR")
        rows.append({
            "time": history[-1].time,
            "tehran_hour": int(tehran_hour(history[-1].time)),
            "baseline_error_atr": abs(baseline.median - actual) / atr,
            "hour_error_atr": abs(hour_aware.median - actual) / atr,
        })
    # The final third is reported separately. It is not used to choose
    # covariates, thresholds or model weights in this evaluator.
    recent = rows[len(rows) * 2 // 3:]
    return {
        "model": model.model_id,
        "context": context,
        "horizon": horizon,
        "stride": stride,
        "all": summary(rows),
        "recent_third": summary(recent),
        "tehran_time_buckets": {
            f"{start:02d}-{start + 6:02d}": summary(
                [row for row in rows if start <= row["tehran_hour"] < start + 6]
            ) for start in (0, 6, 12, 18)
        },
        "scope": "paired forecast error on continuous future M15 bars; no trading P&L estimate",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--symbol", default="XAUUSD_l")
    parser.add_argument("--model", default="autogluon/chronos-2-small")
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--max-windows", type=int, default=48)
    parser.add_argument("--stride", type=int, default=4)
    args = parser.parse_args()
    bars, _ = load_bars(args.db, args.symbol)
    result = compare(bars, ChronosForecaster(model_name(args.model), args.device),
                     stride=args.stride, max_windows=args.max_windows)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
