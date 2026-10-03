"""Ramon M15 historical benchmark lab for external OHLC datasets.

This module is deliberately isolated from the live EA. It compares simple,
non-trained reference forecasters on identical chronological windows and reports
trade-level metrics. External datasets with unknown spread use an explicit
fallback spread supplied by the caller.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
import json
from math import sqrt
from pathlib import Path
from statistics import mean, pstdev
from typing import Sequence

from .compare import MomentumBaseline
from .core import Bar, Forecast, Market, Settings, evaluate
from .history import load_bars


class PreviousBarBaseline:
    """Project the most recent completed-bar move forward; no fitting or future data."""

    def forecast(self, closes: Sequence[float], horizon: int) -> Forecast:
        if len(closes) < 16 or horizon < 1:
            raise ValueError("need >=16 historical closes and a positive horizon")
        move = closes[-1] - closes[-2]
        changes = [b - a for a, b in zip(closes[-16:-1], closes[-15:])]
        width = max(pstdev(changes) * sqrt(horizon) * 1.28, 1e-6)
        path = tuple(closes[-1] + move * n for n in range(1, horizon + 1))
        median = path[-1]
        return Forecast(max(1e-6, median - width), median, median + width, path)


@dataclass(frozen=True, slots=True)
class TradeRecord:
    signal_time: int
    direction: str
    outcome: str
    r: float


def _metrics(trades: Sequence[TradeRecord]) -> dict[str, object]:
    wins = sum(t.outcome == "WIN" for t in trades)
    losses = sum(t.outcome == "LOSS" for t in trades)
    timed_out = sum(t.outcome == "TIMEOUT" for t in trades)
    gross_profit = sum(max(t.r, 0.0) for t in trades)
    gross_loss = -sum(min(t.r, 0.0) for t in trades)
    net = sum(t.r for t in trades)
    equity = peak = drawdown = 0.0
    for trade in trades:
        equity += trade.r
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    resolved = wins + losses
    return {
        "trades": len(trades),
        "buys": sum(t.direction == "BUY" for t in trades),
        "sells": sum(t.direction == "SELL" for t in trades),
        "wins": wins,
        "losses": losses,
        "timed_out": timed_out,
        "resolved_win_rate": round(wins / resolved, 6) if resolved else None,
        "timeout_rate": round(timed_out / len(trades), 6) if trades else None,
        "profit_factor": round(gross_profit / gross_loss, 6) if gross_loss > 0 else None,
        "gross_profit_r": round(gross_profit, 4),
        "gross_loss_r": round(gross_loss, 4),
        "mean_r": round(net / len(trades), 6) if trades else None,
        "net_r": round(net, 4),
        "max_drawdown_r": round(drawdown, 4),
    }


def _window_iso(bars: Sequence[Bar], start: int, end: int) -> dict[str, object]:
    return {
        "start_index": start,
        "end_index_exclusive": end,
        "start_utc": datetime.fromtimestamp(bars[start].time, timezone.utc).isoformat(),
        "end_utc": datetime.fromtimestamp(bars[end - 1].time, timezone.utc).isoformat(),
        "bars": end - start,
    }


def simulate(
    bars: Sequence[Bar],
    spreads: Sequence[int],
    model,
    *,
    symbol: str,
    point: float,
    settings: Settings,
    start: int,
    end: int,
    stride: int,
    fallback_spread_points: int,
    roundtrip_cost_r: float,
) -> tuple[list[TradeRecord], dict[str, int]]:
    """Replay one non-overlapping position stream and preserve per-trade R."""
    if len(bars) != len(spreads) or stride < 1 or point <= 0 or fallback_spread_points <= 0:
        raise ValueError("invalid benchmark input")
    if roundtrip_cost_r < 0 or not 0 <= start < end <= len(bars):
        raise ValueError("invalid benchmark window")

    start_at = max(256, start)
    i = start_at
    decisions = waits = 0
    trades: list[TradeRecord] = []
    while i + settings.horizon < end:
        if (i - start_at) % stride:
            i += 1
            continue
        spread_points = spreads[i] if spreads[i] > 0 else fallback_spread_points
        spread = spread_points * point
        market = Market(
            symbol=symbol,
            timeframe="M15",
            bid=bars[i].close,
            ask=bars[i].close + spread,
            point=point,
            bars=tuple(bars[max(0, i - 255): i + 1]),
        )
        result = evaluate(market, model, settings)
        decisions += 1
        if result.decision == "WAIT":
            waits += 1
            i += 1
            continue

        entry_spread_points = spreads[i + 1] if spreads[i + 1] > 0 else fallback_spread_points
        entry_spread = entry_spread_points * point
        entry = bars[i + 1].open + (entry_spread if result.decision == "BUY" else 0.0)
        stop = entry - result.stop_distance if result.decision == "BUY" else entry + result.stop_distance
        target = entry + result.target_distance if result.decision == "BUY" else entry - result.target_distance
        closed_at = i + settings.horizon
        outcome = "TIMEOUT"
        trade_r = 0.0

        for j in range(i + 1, closed_at + 1):
            bar = bars[j]
            ask_spread_points = spreads[j] if spreads[j] > 0 else fallback_spread_points
            ask_spread = ask_spread_points * point
            if result.decision == "BUY":
                stop_hit = bar.low <= stop
                target_hit = bar.high >= target
            else:
                stop_hit = bar.high + ask_spread >= stop
                target_hit = bar.low + ask_spread <= target
            if stop_hit or target_hit:
                outcome = "LOSS" if stop_hit else "WIN"  # conservative unknown intrabar ordering
                trade_r = -1.0 if stop_hit else result.target_distance / result.stop_distance
                closed_at = j
                break

        if outcome == "TIMEOUT":
            exit_price = bars[closed_at].close
            exit_spread_points = spreads[closed_at] if spreads[closed_at] > 0 else fallback_spread_points
            exit_ask = exit_price + exit_spread_points * point
            delta = exit_price - entry if result.decision == "BUY" else entry - exit_ask
            trade_r = delta / result.stop_distance

        trade_r -= roundtrip_cost_r
        trades.append(TradeRecord(bars[i].time, result.decision, outcome, trade_r))
        i = closed_at + 1

    return trades, {"decisions": decisions, "waits": waits}


def _by_year(trades: Sequence[TradeRecord]) -> dict[str, dict[str, object]]:
    grouped: dict[int, list[TradeRecord]] = defaultdict(list)
    for trade in trades:
        grouped[datetime.fromtimestamp(trade.signal_time, timezone.utc).year].append(trade)
    return {str(year): _metrics(grouped[year]) for year in sorted(grouped)}


def _direction_metrics(trades: Sequence[TradeRecord]) -> dict[str, dict[str, object]]:
    return {
        direction: _metrics([t for t in trades if t.direction == direction])
        for direction in ("BUY", "SELL")
    }


def benchmark_model(
    bars: Sequence[Bar],
    spreads: Sequence[int],
    model,
    *,
    model_name: str,
    symbol: str,
    point: float,
    settings: Settings,
    folds: int,
    stride: int,
    fallback_spread_points: int,
    roundtrip_cost_r: float,
) -> dict[str, object]:
    if folds < 2:
        raise ValueError("folds must be >=2")
    warmup = max(256, len(bars) // 5)
    usable = len(bars) - warmup
    if usable < folds * (settings.horizon + 2):
        raise ValueError("not enough bars for requested folds")

    all_trades, counters = simulate(
        bars, spreads, model, symbol=symbol, point=point, settings=settings,
        start=warmup, end=len(bars), stride=stride,
        fallback_spread_points=fallback_spread_points,
        roundtrip_cost_r=roundtrip_cost_r,
    )

    fold_reports = []
    for fold in range(folds):
        start = warmup + usable * fold // folds
        end = warmup + usable * (fold + 1) // folds
        trades, fold_counters = simulate(
            bars, spreads, model, symbol=symbol, point=point, settings=settings,
            start=start, end=end, stride=stride,
            fallback_spread_points=fallback_spread_points,
            roundtrip_cost_r=roundtrip_cost_r,
        )
        fold_reports.append({
            "fold": fold + 1,
            "window": _window_iso(bars, start, end),
            **fold_counters,
            "metrics": _metrics(trades),
        })

    return {
        "model": model_name,
        "evaluation_window": _window_iso(bars, warmup, len(bars)),
        **counters,
        "metrics": _metrics(all_trades),
        "by_direction": _direction_metrics(all_trades),
        "by_year": _by_year(all_trades),
        "folds": fold_reports,
    }


def benchmark_database(
    db: str | Path,
    *,
    symbol: str = "XAUUSD_KAGGLE",
    point: float = 0.01,
    folds: int = 5,
    stride: int = 4,
    fallback_spread_points: int = 42,
    roundtrip_cost_r: float = 0.0,
) -> dict[str, object]:
    bars, spreads = load_bars(db, symbol)
    if len(bars) < 1200:
        raise ValueError("need >=1200 M15 bars for the historical benchmark")
    settings = replace(
        Settings(),
        require_direction_confirmation=False,
        market_state_policy_enabled=False,
    )
    models = (
        ("previous_bar", PreviousBarBaseline()),
        ("momentum_4bar", MomentumBaseline()),
    )
    return {
        "schema_version": 1,
        "dataset": {
            "db": str(db),
            "symbol": symbol,
            "timeframe": "M15",
            "bars": len(bars),
            "first_utc": datetime.fromtimestamp(bars[0].time, timezone.utc).isoformat(),
            "last_utc": datetime.fromtimestamp(bars[-1].time, timezone.utc).isoformat(),
        },
        "execution_assumptions": {
            "point": point,
            "stride": stride,
            "fallback_spread_points": fallback_spread_points,
            "roundtrip_cost_r": roundtrip_cost_r,
            "unknown_intrabar_order": "stop_first_if_stop_and_target_touch_same_M15_bar",
            "positions": "one_at_a_time_non_overlapping",
            "micro_bars": "unavailable_external_OHLC",
            "market_state_policy": False,
            "direction_confirmation": False,
        },
        "method": {
            "warmup": "first 20% excluded from scored evaluation; still available as prior context",
            "folds": folds,
            "fold_type": "chronological_non_overlapping_evaluation_windows",
            "note": "These reference models are not trained. Trainable models must fit only on data before each fold.",
        },
        "models": {
            name: benchmark_model(
                bars, spreads, model, model_name=name, symbol=symbol, point=point,
                settings=settings, folds=folds, stride=stride,
                fallback_spread_points=fallback_spread_points,
                roundtrip_cost_r=roundtrip_cost_r,
            )
            for name, model in models
        },
        "limitations": [
            "External broker OHLC is not LiteFinance execution history.",
            "Spread is assumed where the external dataset has spread_points=0.",
            "M15 OHLC cannot reconstruct slippage, quote-level timing, or exact intrabar hit order.",
            "This lab does not reproduce RANGE execution or live EA exit management.",
            "Do not change live thresholds from this report alone.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Ramon external M15 historical benchmark lab")
    parser.add_argument("--db", required=True)
    parser.add_argument("--symbol", default="XAUUSD_KAGGLE")
    parser.add_argument("--point", type=float, default=0.01)
    parser.add_argument("--folds", type=int, default=5)
    parser.add_argument("--stride", type=int, default=4)
    parser.add_argument("--fallback-spread", type=int, default=42)
    parser.add_argument("--cost-r", type=float, default=0.0)
    parser.add_argument("--output", default="")
    args = parser.parse_args()
    report = benchmark_database(
        args.db,
        symbol=args.symbol,
        point=args.point,
        folds=args.folds,
        stride=args.stride,
        fallback_spread_points=args.fallback_spread,
        roundtrip_cost_r=args.cost_r,
    )
    payload = json.dumps(report, indent=2, allow_nan=False)
    if args.output:
        Path(args.output).write_text(payload + "\n", encoding="utf-8")
    print(payload)


if __name__ == "__main__":
    main()
