from __future__ import annotations

import argparse
import hashlib
import json
import math
import sqlite3
import statistics
from dataclasses import dataclass, replace
from pathlib import Path

from .validation_lab import Result, Sample, metrics, split_holdout


@dataclass(frozen=True)
class AblationEntry:
    sample: Sample
    chronos_direction: str | None


def _rows_before_entry(bars: list[sqlite3.Row], sample: Sample) -> list[sqlite3.Row]:
    entry_bucket = (sample.entry_time // 900) * 900
    return [row for row in bars if int(row["time"]) < entry_bucket]


def previous_bar_reversal_direction(bars: list[sqlite3.Row], sample: Sample) -> str | None:
    rows = _rows_before_entry(bars, sample)
    if not rows:
        return None
    row = rows[-1]
    op, cl = float(row["open"]), float(row["close"])
    if cl > op:
        return "SELL"
    if cl < op:
        return "BUY"
    return None


def n_bar_momentum_direction(
    bars: list[sqlite3.Row], sample: Sample, *, lookback: int = 4
) -> str | None:
    rows = _rows_before_entry(bars, sample)
    if len(rows) < lookback + 1:
        return None
    start = float(rows[-1 - lookback]["close"])
    end = float(rows[-1]["close"])
    if end > start:
        return "BUY"
    if end < start:
        return "SELL"
    return None


def ma_trend_direction(
    bars: list[sqlite3.Row],
    sample: Sample,
    *,
    fast: int = 4,
    slow: int = 12,
) -> str | None:
    rows = _rows_before_entry(bars, sample)
    if len(rows) < slow:
        return None
    closes = [float(row["close"]) for row in rows]
    fast_ma = statistics.mean(closes[-fast:])
    slow_ma = statistics.mean(closes[-slow:])
    if fast_ma > slow_ma:
        return "BUY"
    if fast_ma < slow_ma:
        return "SELL"
    return None


def matched_ratio_random_direction(sample: Sample, seed: int, buy_ratio: float) -> str:
    key = f"matched:{seed}:{sample.captured}:{sample.entry_time_utc}".encode()
    unit = int(hashlib.sha256(key).hexdigest()[:16], 16) / float(0xFFFFFFFFFFFFFFFF)
    return "BUY" if unit < buy_ratio else "SELL"


def _levels(sample: Sample, direction: str, cost_mult: float) -> tuple[float, float, float]:
    spread = sample.spread * cost_mult
    entry = sample.mid + spread / 2.0 if direction == "BUY" else sample.mid - spread / 2.0
    if direction == "BUY":
        return entry, entry - sample.stop_distance, entry + sample.target_distance
    return entry, entry + sample.stop_distance, entry - sample.target_distance


def replay_with_cost_stress(
    bars: list[sqlite3.Row],
    sample: Sample,
    direction: str,
    *,
    max_bars: int,
    cost_mult: float,
) -> Result | None:
    if direction not in {"BUY", "SELL"}:
        return None
    if sample.stop_distance <= 0 or sample.target_distance <= 0 or cost_mult <= 0:
        return None

    entry, stop, target = _levels(sample, direction, cost_mult)
    next_full_bar = (sample.entry_time // 900 + 1) * 900
    future = [row for row in bars if int(row["time"]) >= next_full_bar][:max_bars]
    if not future:
        return None

    rr = sample.target_distance / sample.stop_distance
    for index, bar in enumerate(future, 1):
        high, low = float(bar["high"]), float(bar["low"])
        if direction == "BUY":
            hit_sl = low <= stop
            hit_tp = high >= target
        else:
            points = int(bar["spread_points"]) if "spread_points" in bar.keys() else 0
            ask_offset = (
                points * sample.point * cost_mult
                if points > 0 and sample.point > 0
                else sample.spread * cost_mult
            )
            hit_sl = high + ask_offset >= stop
            hit_tp = low + ask_offset <= target
        if hit_sl:
            return Result(direction, -1.0, index, "SL")
        if hit_tp:
            return Result(direction, rr, index, "TP")

    close = float(future[-1]["close"])
    if direction == "BUY":
        pnl_price = close - entry
    else:
        points = int(future[-1]["spread_points"]) if "spread_points" in future[-1].keys() else 0
        ask_offset = (
            points * sample.point * cost_mult
            if points > 0 and sample.point > 0
            else sample.spread * cost_mult
        )
        pnl_price = entry - (close + ask_offset)
    return Result(direction, pnl_price / sample.stop_distance, len(future), "TIME")


def _chronos_direction_from_metadata(raw: str | None) -> str | None:
    if not raw:
        return None
    try:
        data = json.loads(raw)
        base = data["decision_audit"]["base"]
        buy_edge = float(base["buy_edge"])
        sell_edge = float(base["sell_edge"])
    except (ValueError, TypeError, KeyError, json.JSONDecodeError):
        return None
    if max(buy_edge, sell_edge) <= 0:
        return None
    return "BUY" if buy_edge >= sell_edge else "SELL"


def load_ablation_data(
    db: str | Path, symbol: str
) -> tuple[list[AblationEntry], list[sqlite3.Row]]:
    path = Path(db).expanduser()
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    try:
        point_rows = list(
            con.execute(
                """SELECT s.spread,h.spread_points
                   FROM decision_samples s
                   JOIN history_bars h
                     ON h.symbol=s.symbol
                    AND h.timeframe='M15'
                    AND h.time=s.signal_bar_time
                   WHERE s.symbol=? AND s.spread>0 AND h.spread_points>0""",
                (symbol,),
            )
        )
        point_values = [
            float(row["spread"]) / int(row["spread_points"])
            for row in point_rows
            if int(row["spread_points"]) > 0
        ]
        point = statistics.median(point_values) if point_values else 0.0

        entries: list[AblationEntry] = []
        for row in con.execute(
            """SELECT s.captured,s.signal_bar_time,s.mid,s.spread,
                      s.stop_distance,s.target_distance,s.model_metadata,
                      t.opened,t.opened_utc_offset_seconds,t.direction,t.actual_fill_price
               FROM trade_outcomes t
               JOIN decision_samples s
                 ON s.sample_key=t.sample_key
                AND s.symbol=t.symbol
               WHERE t.symbol=?
                 AND t.direction IN ('BUY','SELL')
                 AND COALESCE(s.stop_distance,0)>0
                 AND COALESCE(s.target_distance,0)>0
               ORDER BY COALESCE(t.opened-t.opened_utc_offset_seconds,s.captured),t.trade_key""",
            (symbol,),
        ):
            sample = Sample(
                captured=int(row["captured"]),
                signal_bar_time=int(row["signal_bar_time"]),
                entry_time=int(row["opened"]),
                entry_time_utc=(
                    int(row["opened"]) - int(row["opened_utc_offset_seconds"])
                    if row["opened_utc_offset_seconds"] is not None
                    else int(row["captured"])
                ),
                direction=str(row["direction"]),
                mid=float(row["mid"]),
                spread=float(row["spread"]),
                stop_distance=float(row["stop_distance"]),
                target_distance=float(row["target_distance"]),
                point=point,
                actual_fill_price=(
                    float(row["actual_fill_price"]) if row["actual_fill_price"] is not None else None
                ),
            )
            entries.append(
                AblationEntry(
                    sample=sample,
                    chronos_direction=_chronos_direction_from_metadata(row["model_metadata"]),
                )
            )

        bars = list(
            con.execute(
                """SELECT time,open,high,low,close,spread_points
                   FROM history_bars
                   WHERE symbol=? AND timeframe='M15'
                   ORDER BY time""",
                (symbol,),
            )
        )
        return entries, bars
    finally:
        con.close()


def strategy_directions(
    entries: list[AblationEntry],
    bars: list[sqlite3.Row],
    *,
    seed: int,
    momentum_bars: int,
    ma_fast: int,
    ma_slow: int,
) -> dict[str, list[tuple[Sample, str]]]:
    samples = [entry.sample for entry in entries]
    buy_ratio = (
        sum(sample.direction == "BUY" for sample in samples) / len(samples)
        if samples else 0.5
    )
    out: dict[str, list[tuple[Sample, str]]] = {
        "ramon": [],
        "chronos_only": [],
        "prev_reversal": [],
        f"momentum_{momentum_bars}": [],
        f"ma_{ma_fast}_{ma_slow}": [],
        "random_matched": [],
    }
    for entry in entries:
        sample = entry.sample
        out["ramon"].append((sample, sample.direction))
        if entry.chronos_direction is not None:
            out["chronos_only"].append((sample, entry.chronos_direction))
        reversal = previous_bar_reversal_direction(bars, sample)
        if reversal is not None:
            out["prev_reversal"].append((sample, reversal))
        momentum = n_bar_momentum_direction(bars, sample, lookback=momentum_bars)
        if momentum is not None:
            out[f"momentum_{momentum_bars}"].append((sample, momentum))
        ma = ma_trend_direction(bars, sample, fast=ma_fast, slow=ma_slow)
        if ma is not None:
            out[f"ma_{ma_fast}_{ma_slow}"].append((sample, ma))
        out["random_matched"].append(
            (sample, matched_ratio_random_direction(sample, seed, buy_ratio))
        )
    return out


def evaluate_strategies(
    entries: list[AblationEntry],
    bars: list[sqlite3.Row],
    *,
    max_bars: int,
    cost_mult: float,
    seed: int,
    momentum_bars: int,
    ma_fast: int,
    ma_slow: int,
) -> dict[str, list[Result]]:
    directions = strategy_directions(
        entries,
        bars,
        seed=seed,
        momentum_bars=momentum_bars,
        ma_fast=ma_fast,
        ma_slow=ma_slow,
    )
    out: dict[str, list[Result]] = {name: [] for name in directions}
    for name, pairs in directions.items():
        for sample, direction in pairs:
            result = replay_with_cost_stress(
                bars,
                sample,
                direction,
                max_bars=max_bars,
                cost_mult=cost_mult,
            )
            if result is not None:
                out[name].append(result)
    return out


def _fmt(value: float, digits: int = 3) -> str:
    if math.isnan(value):
        return "N/A"
    if math.isinf(value):
        return "inf"
    return f"{value:.{digits}f}"


def print_result_table(title: str, evaluated: dict[str, list[Result]]) -> None:
    print(title)
    print("strategy          trades    WR%    meanR     netR      PF(R)")
    print("-------------------------------------------------------------")
    for name, results in evaluated.items():
        m = metrics(results)
        print(
            f"{name:17s} {int(m['trades']):6d} "
            f"{_fmt(m['win_rate'],2):>6s} "
            f"{_fmt(m['mean_r'],4):>8s} "
            f"{_fmt(m['net_r'],3):>8s} "
            f"{_fmt(m['pf_r'],3):>10s}"
        )
    print()


def print_cost_stress(
    entries: list[AblationEntry],
    bars: list[sqlite3.Row],
    *,
    max_bars: int,
    seed: int,
    momentum_bars: int,
    ma_fast: int,
    ma_slow: int,
) -> None:
    print("=== COST STRESS / HOLDOUT ===")
    print("spread x   Ramon meanR/PF   Chronos meanR/PF   Momentum meanR/PF   MA meanR/PF")
    print("--------------------------------------------------------------------------------")
    for factor in (1.0, 1.5, 2.0):
        evaluated = evaluate_strategies(
            entries,
            bars,
            max_bars=max_bars,
            cost_mult=factor,
            seed=seed,
            momentum_bars=momentum_bars,
            ma_fast=ma_fast,
            ma_slow=ma_slow,
        )
        names = ("ramon", "chronos_only", f"momentum_{momentum_bars}", f"ma_{ma_fast}_{ma_slow}")
        values = []
        for name in names:
            m = metrics(evaluated[name])
            values.append(f"{_fmt(m['mean_r'],3)}/{_fmt(m['pf_r'],2)}")
        print(f"{factor:7.1f}   {values[0]:>15s}   {values[1]:>17s}   {values[2]:>18s}   {values[3]:>12s}")
    print()


def run(
    db: str | Path,
    symbol: str,
    *,
    max_bars: int,
    holdout: float,
    seed: int,
    momentum_bars: int,
    ma_fast: int,
    ma_slow: int,
) -> None:
    entries, bars = load_ablation_data(db, symbol)
    samples = [entry.sample for entry in entries]
    train_samples, test_samples = split_holdout(samples, holdout)
    by_id = {id(entry.sample): entry for entry in entries}
    train_entries = [by_id[id(sample)] for sample in train_samples]
    test_entries = [by_id[id(sample)] for sample in test_samples]

    chronos_coverage = sum(entry.chronos_direction is not None for entry in entries)
    buy_ratio = (
        sum(entry.sample.direction == "BUY" for entry in entries) / len(entries)
        if entries else math.nan
    )

    print("=== RAMON ABLATION LAB ===")
    print(f"Symbol                 : {symbol}")
    print(f"Executed entries       : {len(entries)}")
    print(f"Chronos-only coverage  : {chronos_coverage}/{len(entries)}")
    print(f"Ramon BUY ratio        : {_fmt(100.0 * buy_ratio,2)}%")
    print(f"Momentum baseline      : {momentum_bars} completed M15 bars")
    print(f"MA baseline            : fast={ma_fast}, slow={ma_slow}")
    print("Random matched         : deterministic random with Ramon's observed BUY ratio")
    print("Rules-only exact       : UNAVAILABLE from historical telemetry")
    print("Rules-only reason      : Ramon's intrabar/trend rules are conditioned on Chronos-derived direction/path;")
    print("                         removing Chronos cannot be reconstructed without inventing a new rule system.")
    print("Scope                  : matched Ramon entry times; direction ablation, not entry-timing test")
    print()

    print_result_table(
        "=== FULL HISTORY / ABLATIONS ===",
        evaluate_strategies(
            entries, bars, max_bars=max_bars, cost_mult=1.0, seed=seed,
            momentum_bars=momentum_bars, ma_fast=ma_fast, ma_slow=ma_slow,
        ),
    )
    print_result_table(
        "=== FINAL TIME HOLDOUT / ABLATIONS ===",
        evaluate_strategies(
            test_entries, bars, max_bars=max_bars, cost_mult=1.0, seed=seed,
            momentum_bars=momentum_bars, ma_fast=ma_fast, ma_slow=ma_slow,
        ),
    )
    print_cost_stress(
        test_entries, bars, max_bars=max_bars, seed=seed,
        momentum_bars=momentum_bars, ma_fast=ma_fast, ma_slow=ma_slow,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Ramon matched-timing ablation lab")
    parser.add_argument("--db", default="/data/ramon_history.sqlite3")
    parser.add_argument("--symbol", default="XAUUSD_l")
    parser.add_argument("--max-bars", type=int, default=24)
    parser.add_argument("--holdout", type=float, default=0.30)
    parser.add_argument("--seed", type=int, default=26092212)
    parser.add_argument("--momentum-bars", type=int, default=4)
    parser.add_argument("--ma-fast", type=int, default=4)
    parser.add_argument("--ma-slow", type=int, default=12)
    args = parser.parse_args()

    if args.max_bars < 1:
        parser.error("--max-bars must be >= 1")
    if not 0.10 <= args.holdout <= 0.50:
        parser.error("--holdout must be between 0.10 and 0.50")
    if args.momentum_bars < 1:
        parser.error("--momentum-bars must be >= 1")
    if args.ma_fast < 1 or args.ma_slow <= args.ma_fast:
        parser.error("--ma-slow must be greater than --ma-fast")

    run(
        args.db,
        args.symbol,
        max_bars=args.max_bars,
        holdout=args.holdout,
        seed=args.seed,
        momentum_bars=args.momentum_bars,
        ma_fast=args.ma_fast,
        ma_slow=args.ma_slow,
    )


if __name__ == "__main__":
    main()
