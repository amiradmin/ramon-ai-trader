"""Offline Eliot M5 Chronos-2 feasibility replay; never connects to the live EA."""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import sqlite3
from pathlib import Path

from .core import Bar, Forecaster


@dataclass(frozen=True)
class Row:
    bar: Bar
    spread: float  # XAUUSD price units, recorded on each MT5 bar.


def load_m5(db: str | Path, symbol: str, point: float) -> list[Row]:
    with sqlite3.connect(db) as con:
        rows = con.execute(
            "SELECT time,open,high,low,close,spread_points FROM history_bars "
            "WHERE symbol=? AND timeframe='M5' ORDER BY time", (symbol,),
        ).fetchall()
    if len(rows) < 700:
        raise ValueError("need at least 700 completed M5 bars")
    if any(int(row[5]) <= 0 for row in rows):
        raise ValueError("recorded spreads are required for every M5 bar")
    return [Row(Bar(int(t), float(o), float(h), float(l), float(c)),
                int(spread) * point) for t, o, h, l, c, spread in rows]


def replay(
    rows: list[Row], model: Forecaster, *, start: int, max_decisions: int = 100,
    target_units: float = 5.0, stop_units: float = 6.0,
    units_per_price: float = 1.0, max_spread_points: int = 50,
    point: float = 0.01,
) -> dict[str, object]:
    """Chronological model-only probe; pessimistically count an ambiguous bar as SL.

    Forecast context ends at i; a signal fills at the next bar's open. Thus the
    next bar's spread is used for fills, never to select the signal.
    """
    if (start < 256 or max_decisions < 1 or target_units <= 0 or stop_units <= 0
            or units_per_price <= 0 or point <= 0 or max_spread_points <= 0):
        raise ValueError("invalid replay settings")
    horizon = 3
    target = target_units / units_per_price
    stop = stop_units / units_per_price
    i = start
    decisions = entries = 0
    outcomes: Counter[str] = Counter()
    pnl: list[float] = []
    forecast_moves: list[float] = []
    required_moves: list[float] = []
    while i + horizon < len(rows) and decisions < max_decisions:
        context = rows[i - 255:i + 1]
        if any(b.bar.time - a.bar.time != 300 for a, b in zip(context, context[1:])):
            i += 1
            continue
        if any(rows[j].bar.time - rows[j - 1].bar.time != 300
               for j in range(i + 1, i + horizon + 1)):
            i += 1
            continue
        if rows[i].spread > max_spread_points * point:
            i += 1
            continue
        forecast = model.forecast([row.bar.close for row in context], horizon)
        decisions += 1
        delta = forecast.median - rows[i].bar.close
        forecast_moves.append(abs(delta))
        required_moves.append(target + rows[i].spread)
        # Median must clear both desired profit and known spread. Model-only
        # signals are exploratory; role models have not been trained on M5.
        if abs(delta) <= target + rows[i].spread:
            outcomes["WAIT"] += 1
            i += 1
            continue
        side = 1 if delta > 0 else -1
        first = rows[i + 1]
        entry = first.bar.open + (first.spread if side == 1 else 0.0)
        take = entry + side * target
        loss = entry - side * stop
        result = "TIME"
        gain = 0.0
        for j in range(i + 1, i + horizon + 1):
            row = rows[j]
            high = row.bar.high + (row.spread if side == -1 else 0.0)
            low = row.bar.low + (row.spread if side == -1 else 0.0)
            stopped = low <= loss if side == 1 else high >= loss
            reached = high >= take if side == 1 else low <= take
            if stopped or reached:
                result = "SL" if stopped else "TP"
                gain = -stop_units if stopped else target_units
                break
        if result == "TIME":
            final = rows[i + horizon]
            exit_price = final.bar.close + (final.spread if side == -1 else 0.0)
            gain = side * (exit_price - entry) * units_per_price
        pnl.append(gain)
        outcomes[result] += 1
        entries += 1
        i += horizon  # Single position, no overlapping entries.
    forecast_moves.sort()
    required_moves.sort()

    def percentile(values: list[float], fraction: float) -> float | None:
        if not values:
            return None
        return round(values[int((len(values) - 1) * fraction)], 4)

    return {
        "period_start_utc": datetime.fromtimestamp(rows[start].bar.time, timezone.utc).isoformat(),
        "period_end_utc": datetime.fromtimestamp(rows[min(i, len(rows)-1)].bar.time, timezone.utc).isoformat(),
        "model_decisions": decisions, "entries": entries,
        "outcomes": dict(outcomes), "net_account_units": round(sum(pnl), 3),
        "forecast_abs_move_price_p50": percentile(forecast_moves, .5),
        "forecast_abs_move_price_p90": percentile(forecast_moves, .9),
        "forecast_abs_move_price_max": percentile(forecast_moves, 1),
        "entry_required_move_price_p50": percentile(required_moves, .5),
        "entry_required_move_price_max": percentile(required_moves, 1),
        "mean_units_per_entry": round(sum(pnl) / entries, 4) if entries else None,
        "target_units": target_units, "stop_units": stop_units,
        "units_per_price_assumption": units_per_price,
        "limitations": "M5 bar spread proxy; stop first on ambiguous bars; no slippage, fees or M5 role models",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Offline Eliot M5 Chronos-2 short-trade probe")
    parser.add_argument("--db", default="/data/ramon_history.sqlite3")
    parser.add_argument("--symbol", default="XAUUSD_l")
    parser.add_argument("--model", default="autogluon/chronos-2-small")
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--max-decisions", type=int, default=100)
    parser.add_argument("--units-per-price", type=float, required=True,
                        help="MT5 OrderCalcProfit: account units per 1.0 XAUUSD move at minimum volume")
    args = parser.parse_args()
    from .model import ChronosForecaster

    rows = load_m5(args.db, args.symbol, 0.01)
    # Entire final fifth remains out-of-sample; no optimization on those bars.
    result = replay(rows, ChronosForecaster(args.model, args.device),
                    start=len(rows) * 4 // 5, max_decisions=args.max_decisions,
                    units_per_price=args.units_per_price)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
