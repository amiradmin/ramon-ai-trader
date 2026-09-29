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
    result = {
        "windows": len(rows),
        "baseline_mae_atr": round(baseline, 5),
        "hour_mae_atr": round(clock, 5),
        "difference_mae_atr": round(clock - baseline, 5),
        "hour_better_windows": sum(row["hour_error_atr"] < row["baseline_error_atr"]
                                   for row in rows),
    }
    for name in ("baseline", "hour"):
        trades = [row for row in rows if row[f"{name}_side"] != "WAIT"]
        result[f"{name}_direction_count"] = len(trades)
        result[f"{name}_direction_hits"] = sum(row[f"{name}_net_atr"] > 0 for row in trades)
        result[f"{name}_direction_hit_rate"] = (round(result[f"{name}_direction_hits"] / len(trades), 4)
                                                 if trades else None)
        result[f"{name}_mean_net_atr"] = (round(sum(row[f"{name}_net_atr"] for row in trades)
                                                  / len(trades), 5) if trades else None)
        result[f"{name}_path_mean_net_atr"] = (
            round(sum(row[f"{name}_path_net_atr"] for row in trades) / len(trades), 5)
            if trades else None
        )
        result[f"{name}_path_wins"] = sum(row[f"{name}_path_net_atr"] > 0 for row in trades)
    result["realized_best_side_counts"] = {
        side: sum(row["realized_best_side"] == side for row in rows)
        for side in ("BUY", "SELL", "WAIT")
    }
    return result


def directional_result(median: float, last_close: float, actual: float,
                       spread: float, atr: float) -> tuple[str, float]:
    """Indicative bid-to-bid hold return after entry/exit spread; no stops."""
    if median > last_close + spread:
        return "BUY", (actual - last_close - spread) / atr
    if median < last_close - spread:
        return "SELL", (last_close - actual - spread) / atr
    return "WAIT", 0.0


def path_result(side: str, midpoint: float, future: tuple[Bar, ...],
                spread: float, atr: float, *, stop_atr: float = 1.5,
                target_atr: float = 3.0) -> float:
    """Indicative M15 first-touch return; same-candle TP/SL resolves as loss.

    High/low and close are treated as midpoints. One observed entry spread is
    applied on both sides; intrabar spread and fill slippage are unavailable.
    """
    if side == "WAIT":
        return 0.0
    if side not in ("BUY", "SELL") or not future or atr <= 0 or spread < 0:
        raise ValueError("invalid path inputs")
    half = spread / 2
    if side == "BUY":
        entry = midpoint + half
        stop = entry - stop_atr * atr
        target = entry + target_atr * atr
        for bar in future:
            if bar.low - half <= stop:
                return -stop_atr
            if bar.high - half >= target:
                return target_atr
        return (future[-1].close - half - entry) / atr
    entry = midpoint - half
    stop = entry + stop_atr * atr
    target = entry - target_atr * atr
    for bar in future:
        if bar.high + half >= stop:
            return -stop_atr
        if bar.low + half <= target:
            return target_atr
    return (entry - future[-1].close - half) / atr


def market_regime(history: tuple[Bar, ...], atr: float) -> str:
    """Transparent research-only labels from prices known at the decision time."""
    if len(history) < 13 or atr <= 0:
        raise ValueError("regime needs 13 completed bars and positive ATR")
    recent = history[-12:]
    move = (history[-1].close - history[-13].close) / atr
    range_atr = (max(bar.high for bar in recent) - min(bar.low for bar in recent)) / atr
    if range_atr >= 6 and abs(move) < 1.5:
        return "VOLATILE_REVERSAL"
    if move >= 2:
        return "UP_TREND"
    if move <= -2:
        return "DOWN_TREND"
    return "RANGE_OR_UNCLEAR"


def compare(bars: tuple[Bar, ...], model: ChronosForecaster, *, context: int = 256,
            horizon: int = 4, stride: int = 4, max_windows: int = 48,
            spreads: tuple[float, ...] | None = None) -> dict:
    if spreads is not None and len(spreads) != len(bars):
        raise ValueError("one spread per bar required")
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
        spread = spreads[index] if spreads is not None else 0.0
        if not isfinite(spread) or spread < 0:
            raise ValueError("invalid historical spread")
        baseline_side, baseline_net = directional_result(
            baseline.median, history[-1].close, actual, spread, atr)
        hour_side, hour_net = directional_result(
            hour_aware.median, history[-1].close, actual, spread, atr)
        future = bars[index + 1:index + horizon + 1]
        buy_path = path_result("BUY", history[-1].close, future, spread, atr)
        sell_path = path_result("SELL", history[-1].close, future, spread, atr)
        realized_best = ("WAIT" if max(buy_path, sell_path) <= 0 else
                         "BUY" if buy_path >= sell_path else "SELL")
        rows.append({
            "time": history[-1].time,
            "tehran_hour": int(tehran_hour(history[-1].time)),
            "regime": market_regime(history, atr),
            "baseline_error_atr": abs(baseline.median - actual) / atr,
            "hour_error_atr": abs(hour_aware.median - actual) / atr,
            "baseline_side": baseline_side,
            "baseline_net_atr": baseline_net,
            "baseline_path_net_atr": path_result(
                baseline_side, history[-1].close, future, spread, atr),
            "hour_side": hour_side,
            "hour_net_atr": hour_net,
            "hour_path_net_atr": path_result(
                hour_side, history[-1].close, future, spread, atr),
            "realized_best_side": realized_best,
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
        "market_regimes": {
            regime: summary([row for row in rows if row["regime"] == regime])
            for regime in ("UP_TREND", "DOWN_TREND", "RANGE_OR_UNCLEAR",
                           "VOLATILE_REVERSAL")
        },
        "scope": "paired M15 endpoint and first-touch direction audit; path assumes constant "
                 "entry spread, no slippage, midpoint OHLC and adverse same-bar ordering. "
                 "No live quotes or execution; not realized P&L or calibrated probabilities",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--symbol", default="XAUUSD_l")
    parser.add_argument("--model", default="autogluon/chronos-2-small")
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--max-windows", type=int, default=48)
    parser.add_argument("--stride", type=int, default=4)
    parser.add_argument("--point", type=float, required=True,
                        help="Broker SYMBOL_POINT, e.g. 0.01; used to convert recorded spread points")
    parser.add_argument("--horizon", type=int, choices=(1, 4), action="append",
                        help="Forecast steps to assess; defaults to both 1 and 4")
    args = parser.parse_args()
    bars, spread_points = load_bars(args.db, args.symbol)
    parser_point = float(args.point)
    if not isfinite(parser_point) or parser_point <= 0:
        raise ValueError("positive broker point required")
    model = ChronosForecaster(model_name(args.model), args.device)
    result = {
        f"horizon_{horizon}": compare(
            bars, model, horizon=horizon, stride=args.stride,
            max_windows=args.max_windows,
            spreads=tuple(points * parser_point for points in spread_points))
        for horizon in (args.horizon or [1, 4])
    }
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
