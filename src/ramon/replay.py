from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from typing import Sequence

from .core import Bar, Forecaster, Market, Settings, evaluate
from .history import load_bars
from .model import ChronosForecaster, model_name


@dataclass(frozen=True, slots=True)
class ReplayResult:
    bars: int
    decisions: int
    buys: int
    sells: int
    wins: int
    losses: int
    timed_out: int
    net_r: float


def replay(
    bars: Sequence[Bar],
    spreads: Sequence[int],
    model: Forecaster,
    *,
    point: float = 0.01,
    settings: Settings = Settings(),
    start: int | None = None,
    stride: int = 1,
    fallback_spread_points: int = 42,
) -> ReplayResult:
    """One-position historical replay with next-bar entry and conservative fills."""
    if len(bars) != len(spreads) or stride < 1 or point <= 0 or fallback_spread_points <= 0:
        raise ValueError("invalid replay input")
    start_at = max(256, start if start is not None else len(bars) * 4 // 5)
    i = start_at
    decisions = buys = sells = wins = losses = timed_out = 0
    net_r = 0.0
    while i + settings.horizon < len(bars):
        if (i - start_at) % stride:
            i += 1
            continue
        spread = (spreads[i] if spreads[i] > 0 else fallback_spread_points) * point
        market = Market(
            symbol="XAUUSD_l",
            timeframe="M15",
            bid=bars[i].close,
            ask=bars[i].close + spread,
            point=point,
            bars=tuple(bars[max(0, i - 255) : i + 1]),
        )
        result = evaluate(market, model, settings)
        decisions += 1
        if result.decision == "WAIT":
            i += 1
            continue
        buys += result.decision == "BUY"
        sells += result.decision == "SELL"
        entry_spread = (spreads[i + 1] if spreads[i + 1] > 0 else fallback_spread_points) * point
        entry = bars[i + 1].open + (entry_spread if result.decision == "BUY" else 0)
        stop = entry - result.stop_distance if result.decision == "BUY" else entry + result.stop_distance
        target = entry + result.target_distance if result.decision == "BUY" else entry - result.target_distance
        closed_at = i + settings.horizon
        outcome = "TIMEOUT"
        exit_price = bars[closed_at].close
        for j in range(i + 1, closed_at + 1):
            bar = bars[j]
            ask_spread = (spreads[j] if spreads[j] > 0 else fallback_spread_points) * point
            if result.decision == "BUY":
                stop_hit, target_hit = bar.low <= stop, bar.high >= target
            else:
                stop_hit = bar.high + ask_spread >= stop
                target_hit = bar.low + ask_spread <= target
            if stop_hit or target_hit:
                # Unknown intrabar order: count stop first if both were touched.
                outcome = "LOSS" if stop_hit else "WIN"
                exit_price = stop if stop_hit else target
                closed_at = j
                break
        if outcome == "WIN":
            wins += 1
            net_r += result.target_distance / result.stop_distance
        elif outcome == "LOSS":
            losses += 1
            net_r -= 1.0
        else:
            timed_out += 1
            exit_ask = exit_price + (
                spreads[closed_at] if spreads[closed_at] > 0 else fallback_spread_points
            ) * point
            delta = (
                exit_price - entry
                if result.decision == "BUY"
                else entry - exit_ask
            )
            net_r += delta / result.stop_distance
        i = closed_at + 1  # no overlapping positions
    return ReplayResult(len(bars), decisions, buys, sells, wins, losses, timed_out, round(net_r, 4))


def main() -> None:
    parser = argparse.ArgumentParser(description="Offline historical Chronos-2 evaluation")
    parser.add_argument("--db", required=True)
    parser.add_argument("--symbol", default="XAUUSD_l")
    parser.add_argument("--point", type=float, default=0.01)
    parser.add_argument("--model", default="autogluon/chronos-2-small")
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--stride", type=int, default=4)
    parser.add_argument("--fallback-spread", type=int, default=42)
    args = parser.parse_args()
    bars, spreads = load_bars(args.db, args.symbol)
    if len(bars) < 600:
        raise SystemExit("need >=600 bars for a meaningful chronological holdout")
    model = ChronosForecaster(model_name(args.model), args.device)
    print(json.dumps(asdict(replay(
        bars, spreads, model, point=args.point, stride=args.stride,
        fallback_spread_points=args.fallback_spread,
    )), indent=2))


if __name__ == "__main__":
    main()
