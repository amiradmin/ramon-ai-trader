from __future__ import annotations

import argparse
import hashlib
import math
import random
import sqlite3
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Sample:
    captured: int
    signal_bar_time: int
    entry_time: int
    direction: str
    mid: float
    spread: float
    stop_distance: float
    target_distance: float


@dataclass(frozen=True)
class Result:
    direction: str
    outcome_r: float
    bars_held: int
    exit_kind: str


def _entry_price(sample: Sample, direction: str) -> float:
    return sample.mid + sample.spread / 2.0 if direction == "BUY" else sample.mid - sample.spread / 2.0


def _levels(sample: Sample, direction: str) -> tuple[float, float, float]:
    entry = _entry_price(sample, direction)
    if direction == "BUY":
        return entry, entry - sample.stop_distance, entry + sample.target_distance
    return entry, entry + sample.stop_distance, entry - sample.target_distance


def replay_direction(
    bars: list[sqlite3.Row],
    sample: Sample,
    direction: str,
    *,
    max_bars: int,
) -> Result | None:
    """Replay one baseline with the sample's stored SL/TP distances.

    Ambiguous same-bar TP+SL hits are resolved conservatively as SL-first because
    M15 OHLC does not reveal intrabar ordering.
    """
    if direction not in {"BUY", "SELL"} or sample.stop_distance <= 0 or sample.target_distance <= 0:
        return None
    entry, stop, target = _levels(sample, direction)
    # Use only complete M15 bars strictly after the actual fill's M15 bucket.
    # This avoids contaminating the replay with pre-entry extremes from the entry bar.
    next_full_bar = (sample.entry_time // 900 + 1) * 900
    future = [row for row in bars if int(row["time"]) >= next_full_bar][:max_bars]
    if not future:
        return None

    rr = sample.target_distance / sample.stop_distance
    for index, bar in enumerate(future, 1):
        high = float(bar["high"])
        low = float(bar["low"])
        if direction == "BUY":
            hit_sl = low <= stop
            hit_tp = high >= target
        else:
            hit_sl = high >= stop
            hit_tp = low <= target

        if hit_sl:
            return Result(direction, -1.0, index, "SL")
        if hit_tp:
            return Result(direction, rr, index, "TP")

    close = float(future[-1]["close"])
    pnl_price = close - entry if direction == "BUY" else entry - close
    outcome_r = pnl_price / sample.stop_distance
    return Result(direction, outcome_r, len(future), "TIME")


def _deterministic_random_direction(sample: Sample, seed: int) -> str:
    key = f"{seed}:{sample.captured}:{sample.entry_time}".encode()
    value = int(hashlib.sha256(key).hexdigest()[:16], 16)
    return "BUY" if value % 2 == 0 else "SELL"


def previous_bar_direction(bars: list[sqlite3.Row], sample: Sample) -> str | None:
    previous = None
    for row in bars:
        entry_bar = (sample.entry_time // 900) * 900
        if int(row["time"]) < entry_bar:
            previous = row
        else:
            break
    if previous is None:
        return None
    op = float(previous["open"])
    cl = float(previous["close"])
    if cl > op:
        return "BUY"
    if cl < op:
        return "SELL"
    return None


def metrics(results: list[Result]) -> dict[str, float]:
    if not results:
        return {"trades": 0, "win_rate": math.nan, "mean_r": math.nan, "net_r": 0.0, "pf_r": math.nan}
    wins = [r.outcome_r for r in results if r.outcome_r > 0]
    losses = [r.outcome_r for r in results if r.outcome_r < 0]
    gains = sum(wins)
    loss_abs = abs(sum(losses))
    return {
        "trades": float(len(results)),
        "win_rate": 100.0 * len(wins) / len(results),
        "mean_r": sum(r.outcome_r for r in results) / len(results),
        "net_r": sum(r.outcome_r for r in results),
        "pf_r": gains / loss_abs if loss_abs else math.inf,
    }


def load_data(db: str | Path, symbol: str) -> tuple[list[Sample], list[sqlite3.Row]]:
    con = sqlite3.connect(Path(db).expanduser())
    con.row_factory = sqlite3.Row
    try:
        # Benchmark only times where Ramon actually entered a real trade.
        # This avoids treating every 30-second model snapshot as an independent trade.
        samples = [
            Sample(
                captured=int(row["captured"]),
                signal_bar_time=int(row["signal_bar_time"]),
                entry_time=int(row["opened"]),
                direction=str(row["trade_direction"]),
                mid=float(row["mid"]),
                spread=float(row["spread"]),
                stop_distance=float(row["stop_distance"]),
                target_distance=float(row["target_distance"]),
            )
            for row in con.execute(
                """SELECT s.captured,s.signal_bar_time,t.opened,
                          t.direction AS trade_direction,
                          s.mid,s.spread,s.stop_distance,s.target_distance
                   FROM trade_outcomes t
                   JOIN decision_samples s
                     ON s.sample_key=t.sample_key
                    AND s.symbol=t.symbol
                   WHERE t.symbol=?
                     AND t.direction IN ('BUY','SELL')
                     AND COALESCE(s.stop_distance,0)>0
                     AND COALESCE(s.target_distance,0)>0
                   ORDER BY t.opened,t.trade_key""",
                (symbol,),
            )
        ]
        bars = list(
            con.execute(
                """SELECT time,open,high,low,close
                   FROM history_bars
                   WHERE symbol=? AND timeframe='M15'
                   ORDER BY time""",
                (symbol,),
            )
        )
        return samples, bars
    finally:
        con.close()


def evaluate(
    samples: list[Sample],
    bars: list[sqlite3.Row],
    *,
    max_bars: int = 24,
    random_seed: int = 26092212,
) -> dict[str, list[Result]]:
    out: dict[str, list[Result]] = {
        "ramon": [],
        "random": [],
        "always_buy": [],
        "always_sell": [],
        "previous_bar": [],
    }
    for sample in samples:
        choices = {
            "ramon": sample.direction,
            "random": _deterministic_random_direction(sample, random_seed),
            "always_buy": "BUY",
            "always_sell": "SELL",
            "previous_bar": previous_bar_direction(bars, sample),
        }
        for name, direction in choices.items():
            if direction is None:
                continue
            result = replay_direction(bars, sample, direction, max_bars=max_bars)
            if result is not None:
                out[name].append(result)
    return out


def split_holdout(samples: list[Sample], fraction: float) -> tuple[list[Sample], list[Sample]]:
    if not samples:
        return [], []
    cut = max(1, min(len(samples) - 1, int(len(samples) * (1.0 - fraction))))
    return samples[:cut], samples[cut:]


def _fmt(value: float, digits: int = 3) -> str:
    if math.isnan(value):
        return "N/A"
    if math.isinf(value):
        return "inf"
    return f"{value:.{digits}f}"


def print_table(title: str, evaluated: dict[str, list[Result]]) -> None:
    print(title)
    print("strategy       trades    WR%    meanR     netR      PF(R)")
    print("----------------------------------------------------------")
    for name in ("ramon", "random", "always_buy", "always_sell", "previous_bar"):
        m = metrics(evaluated[name])
        print(
            f"{name:13s} {int(m['trades']):6d} "
            f"{_fmt(m['win_rate'],2):>6s} "
            f"{_fmt(m['mean_r'],4):>8s} "
            f"{_fmt(m['net_r'],3):>8s} "
            f"{_fmt(m['pf_r'],3):>10s}"
        )
    print()


def run(db: str | Path, symbol: str, *, max_bars: int, holdout: float, seed: int) -> None:
    samples, bars = load_data(db, symbol)
    train, test = split_holdout(samples, holdout)
    print("=== RAMON VALIDATION LAB / BASELINE BENCHMARK ===")
    print(f"Symbol          : {symbol}")
    print(f"Executed entries: {len(samples)}")
    print(f"Holdout fraction: {holdout:.0%}")
    print(f"Replay horizon  : {max_bars} M15 bars")
    print("Scope           : real executed Ramon entries only")
    print("Replay start    : first complete M15 bar after actual fill")
    print("Costs           : stored bid/ask spread from matched entry snapshot")
    print("Exit model      : stored SL/TP distance; same-bar ambiguity = SL-first")
    print("Interpretation  : research benchmark only; not live execution logic")
    print()
    print_table("=== FULL HISTORY ===", evaluate(samples, bars, max_bars=max_bars, random_seed=seed))
    print_table("=== EARLY / DEVELOPMENT WINDOW ===", evaluate(train, bars, max_bars=max_bars, random_seed=seed))
    print_table("=== FINAL TIME HOLDOUT (OUT-OF-SAMPLE) ===", evaluate(test, bars, max_bars=max_bars, random_seed=seed))


def main() -> None:
    parser = argparse.ArgumentParser(description="Ramon no-skill/simple baseline validation lab")
    parser.add_argument("--db", default="/data/ramon_history.sqlite3")
    parser.add_argument("--symbol", default="XAUUSD_l")
    parser.add_argument("--max-bars", type=int, default=24)
    parser.add_argument("--holdout", type=float, default=0.30)
    parser.add_argument("--seed", type=int, default=26092212)
    args = parser.parse_args()
    if args.max_bars < 1:
        parser.error("--max-bars must be >= 1")
    if not 0.10 <= args.holdout <= 0.50:
        parser.error("--holdout must be between 0.10 and 0.50")
    run(args.db, args.symbol, max_bars=args.max_bars, holdout=args.holdout, seed=args.seed)


if __name__ == "__main__":
    main()
