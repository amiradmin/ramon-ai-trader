from __future__ import annotations

import argparse
import hashlib
import math
import random
import statistics
import sqlite3
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Sample:
    captured: int
    signal_bar_time: int
    entry_time: int
    entry_time_utc: int
    direction: str
    mid: float
    spread: float
    stop_distance: float
    target_distance: float
    point: float = 0.0
    actual_fill_price: float | None = None


@dataclass(frozen=True)
class Result:
    direction: str
    outcome_r: float
    bars_held: int
    exit_kind: str


@dataclass(frozen=True)
class WalkForwardFold:
    number: int
    development: tuple[Sample, ...]
    test: tuple[Sample, ...]
    purge_seconds: int


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
            spread_points = int(bar["spread_points"]) if "spread_points" in bar.keys() else 0
            ask_offset = (
                spread_points * sample.point
                if spread_points > 0 and sample.point > 0
                else sample.spread
            )
            ask_high = high + ask_offset
            ask_low = low + ask_offset
            hit_sl = ask_high >= stop
            hit_tp = ask_low <= target

        if hit_sl:
            return Result(direction, -1.0, index, "SL")
        if hit_tp:
            return Result(direction, rr, index, "TP")

    close = float(future[-1]["close"])
    if direction == "BUY":
        pnl_price = close - entry
    else:
        spread_points = (
            int(future[-1]["spread_points"])
            if "spread_points" in future[-1].keys()
            else 0
        )
        ask_offset = (
            spread_points * sample.point
            if spread_points > 0 and sample.point > 0
            else sample.spread
        )
        pnl_price = entry - (close + ask_offset)
    outcome_r = pnl_price / sample.stop_distance
    return Result(direction, outcome_r, len(future), "TIME")


def _deterministic_random_direction(sample: Sample, seed: int) -> str:
    key = f"{seed}:{sample.captured}:{sample.entry_time_utc}".encode()
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

        # Benchmark only times where Ramon actually entered a real trade.
        # entry_time remains broker-clock for matching CopyRates bars.
        # entry_time_utc is canonical for chronological split/purge; legacy rows
        # without a stored offset fall back to the UTC server receipt timestamp.
        samples = [
            Sample(
                captured=int(row["captured"]),
                signal_bar_time=int(row["signal_bar_time"]),
                entry_time=int(row["opened"]),
                entry_time_utc=(
                    int(row["opened"]) - int(row["opened_utc_offset_seconds"])
                    if row["opened_utc_offset_seconds"] is not None
                    else int(row["captured"])
                ),
                direction=str(row["trade_direction"]),
                mid=float(row["mid"]),
                spread=float(row["spread"]),
                stop_distance=float(row["stop_distance"]),
                target_distance=float(row["target_distance"]),
                point=point,
                actual_fill_price=(
                    float(row["actual_fill_price"])
                    if row["actual_fill_price"] is not None
                    else None
                ),
            )
            for row in con.execute(
                """SELECT s.captured,s.signal_bar_time,t.opened,
                          t.opened_utc_offset_seconds,t.actual_fill_price,
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
                   ORDER BY COALESCE(t.opened-t.opened_utc_offset_seconds,s.captured),t.trade_key""",
                (symbol,),
            )
        ]
        bars = list(
            con.execute(
                """SELECT time,open,high,low,close,spread_points
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


def walk_forward_folds(
    samples: list[Sample],
    *,
    folds: int,
    max_bars: int,
    embargo_bars: int,
    initial_fraction: float = 0.40,
) -> list[WalkForwardFold]:
    """Create chronological expanding-window folds with a purge/embargo gap.

    The development window ends before the test window by at least the replay
    horizon plus the requested embargo. This prevents labels/outcomes whose
    future path overlaps the test period from leaking into development.
    """
    if folds < 2 or len(samples) < folds + 2:
        return []
    ordered = sorted(samples, key=lambda row: row.entry_time_utc)
    initial = max(1, min(len(ordered) - folds, int(len(ordered) * initial_fraction)))
    remaining = len(ordered) - initial
    base = remaining // folds
    extra = remaining % folds
    if base <= 0:
        return []

    purge_seconds = (max_bars + embargo_bars) * 900
    result: list[WalkForwardFold] = []
    start = initial
    for number in range(1, folds + 1):
        size = base + (1 if number <= extra else 0)
        stop = start + size
        test = ordered[start:stop]
        if not test:
            break
        test_start = test[0].entry_time_utc
        development = tuple(
            row for row in ordered[:start]
            if row.entry_time_utc + purge_seconds < test_start
        )
        result.append(
            WalkForwardFold(
                number=number,
                development=development,
                test=tuple(test),
                purge_seconds=purge_seconds,
            )
        )
        start = stop
    return result


def _baseline_direction(name: str, sample: Sample, bars: list[sqlite3.Row], seed: int) -> str | None:
    if name == "random":
        return _deterministic_random_direction(sample, seed)
    if name == "always_buy":
        return "BUY"
    if name == "always_sell":
        return "SELL"
    if name == "previous_bar":
        return previous_bar_direction(bars, sample)
    raise ValueError(f"unknown baseline: {name}")


def ambiguous_fraction(
    samples: list[Sample],
    bars: list[sqlite3.Row],
    *,
    direction_mode: str,
    max_bars: int,
    seed: int,
) -> tuple[int, int, float]:
    """Count paths where the same M15 bar touches both SL and TP.

    This is a simulator-quality diagnostic. OHLC cannot reveal intrabar ordering,
    so the main replay uses conservative SL-first ordering.
    """
    ambiguous = total = 0
    for sample in samples:
        direction = sample.direction if direction_mode == "ramon" else _baseline_direction(
            direction_mode, sample, bars, seed
        )
        if direction is None:
            continue
        entry, stop, target = _levels(sample, direction)
        del entry
        next_full_bar = (sample.entry_time // 900 + 1) * 900
        future = [row for row in bars if int(row["time"]) >= next_full_bar][:max_bars]
        if not future:
            continue
        total += 1
        for bar in future:
            high = float(bar["high"])
            low = float(bar["low"])
            if direction == "BUY":
                hit_sl = low <= stop
                hit_tp = high >= target
            else:
                spread_points = int(bar["spread_points"]) if "spread_points" in bar.keys() else 0
                ask_offset = (
                    spread_points * sample.point
                    if spread_points > 0 and sample.point > 0
                    else sample.spread
                )
                hit_sl = high + ask_offset >= stop
                hit_tp = low + ask_offset <= target
            if hit_sl and hit_tp:
                ambiguous += 1
                break
            if hit_sl or hit_tp:
                break
    return ambiguous, total, (100.0 * ambiguous / total if total else math.nan)


def multi_seed_random_distribution(
    samples: list[Sample],
    bars: list[sqlite3.Row],
    *,
    max_bars: int,
    seeds: int,
    seed_base: int,
) -> tuple[list[float], float, float]:
    """Return random-baseline mean-R distribution and Ramon's percentile.

    BUY/SELL counterfactuals are replayed once per entry and reused for all
    random seeds so 1,000+ seeds remain cheap and deterministic.
    """
    if seeds <= 0:
        return [], math.nan, math.nan

    cached: list[tuple[Sample, Result, Result, Result]] = []
    for sample in samples:
        ramon = replay_direction(bars, sample, sample.direction, max_bars=max_bars)
        buy = replay_direction(bars, sample, "BUY", max_bars=max_bars)
        sell = replay_direction(bars, sample, "SELL", max_bars=max_bars)
        if ramon is not None and buy is not None and sell is not None:
            cached.append((sample, ramon, buy, sell))

    if not cached:
        return [], math.nan, math.nan

    ramon_mean = statistics.mean(item[1].outcome_r for item in cached)
    random_means: list[float] = []
    for offset in range(seeds):
        seed = seed_base + offset
        values = []
        for sample, _ramon, buy, sell in cached:
            direction = _deterministic_random_direction(sample, seed)
            values.append(buy.outcome_r if direction == "BUY" else sell.outcome_r)
        random_means.append(statistics.mean(values))

    at_or_below = sum(value <= ramon_mean for value in random_means)
    percentile = 100.0 * at_or_below / len(random_means)
    return random_means, ramon_mean, percentile


def paired_mean_r_deltas(
    samples: list[Sample],
    bars: list[sqlite3.Row],
    *,
    baseline: str,
    max_bars: int,
    seed: int,
) -> list[float]:
    """Return paired Ramon-minus-baseline R differences on identical entries."""
    deltas: list[float] = []
    for sample in samples:
        ramon = replay_direction(bars, sample, sample.direction, max_bars=max_bars)
        direction = _baseline_direction(baseline, sample, bars, seed)
        if ramon is None or direction is None:
            continue
        other = replay_direction(bars, sample, direction, max_bars=max_bars)
        if other is not None:
            deltas.append(ramon.outcome_r - other.outcome_r)
    return deltas


def bootstrap_mean_ci(
    values: list[float],
    *,
    iterations: int = 4000,
    confidence: float = 0.95,
    seed: int = 26092212,
) -> tuple[float, float, float]:
    """Deterministic non-parametric bootstrap CI for the arithmetic mean."""
    if not values:
        return math.nan, math.nan, math.nan
    if len(values) == 1:
        return values[0], values[0], values[0]
    rng = random.Random(seed)
    n = len(values)
    means = []
    for _ in range(iterations):
        means.append(sum(values[rng.randrange(n)] for _ in range(n)) / n)
    means.sort()
    alpha = (1.0 - confidence) / 2.0
    lo_index = max(0, min(iterations - 1, int(alpha * iterations)))
    hi_index = max(0, min(iterations - 1, int((1.0 - alpha) * iterations) - 1))
    return statistics.mean(values), means[lo_index], means[hi_index]


def print_walk_forward(
    samples: list[Sample],
    bars: list[sqlite3.Row],
    *,
    folds: int,
    max_bars: int,
    embargo_bars: int,
    seed: int,
    bootstrap_iterations: int,
    random_seeds: int,
) -> None:
    wf = walk_forward_folds(
        samples,
        folds=folds,
        max_bars=max_bars,
        embargo_bars=embargo_bars,
    )
    print("=== PURGED WALK-FORWARD OUT-OF-SAMPLE ===")
    if not wf:
        print("Not enough executed entries for requested fold configuration.")
        print()
        return

    print(
        f"Folds={len(wf)} | purge={max_bars} bars replay horizon | "
        f"embargo={embargo_bars} bars | bootstrap={bootstrap_iterations}"
    )
    print(
        "fold  dev  test   Ramon meanR/PF   Random meanR/PF   PrevBar meanR/PF   "
        "ΔR-Random [95% CI]        ΔR-Prev [95% CI]"
    )
    print("-" * 132)

    positive_vs_random = 0
    positive_vs_previous = 0
    all_ramon: list[Result] = []
    all_random: list[Result] = []
    all_previous: list[Result] = []
    all_delta_random: list[float] = []
    all_delta_previous: list[float] = []

    for fold in wf:
        evaluated = evaluate(list(fold.test), bars, max_bars=max_bars, random_seed=seed)
        rm = metrics(evaluated["ramon"])
        rd = metrics(evaluated["random"])
        pm = metrics(evaluated["previous_bar"])
        dr = paired_mean_r_deltas(
            list(fold.test), bars, baseline="random", max_bars=max_bars, seed=seed
        )
        dp = paired_mean_r_deltas(
            list(fold.test), bars, baseline="previous_bar", max_bars=max_bars, seed=seed
        )
        dr_mean, dr_lo, dr_hi = bootstrap_mean_ci(
            dr, iterations=bootstrap_iterations, seed=seed + fold.number * 11
        )
        dp_mean, dp_lo, dp_hi = bootstrap_mean_ci(
            dp, iterations=bootstrap_iterations, seed=seed + fold.number * 17
        )
        positive_vs_random += int(not math.isnan(dr_mean) and dr_mean > 0)
        positive_vs_previous += int(not math.isnan(dp_mean) and dp_mean > 0)

        all_ramon.extend(evaluated["ramon"])
        all_random.extend(evaluated["random"])
        all_previous.extend(evaluated["previous_bar"])
        all_delta_random.extend(dr)
        all_delta_previous.extend(dp)

        print(
            f"{fold.number:>4d} {len(fold.development):>4d} {len(fold.test):>5d}   "
            f"{_fmt(rm['mean_r'],3):>6s}/{_fmt(rm['pf_r'],2):<5s}       "
            f"{_fmt(rd['mean_r'],3):>6s}/{_fmt(rd['pf_r'],2):<5s}        "
            f"{_fmt(pm['mean_r'],3):>6s}/{_fmt(pm['pf_r'],2):<5s}       "
            f"{_fmt(dr_mean,3):>6s} [{_fmt(dr_lo,3):>6s},{_fmt(dr_hi,3):>6s}]   "
            f"{_fmt(dp_mean,3):>6s} [{_fmt(dp_lo,3):>6s},{_fmt(dp_hi,3):>6s}]"
        )

    aggregate = {
        "ramon": metrics(all_ramon),
        "random": metrics(all_random),
        "previous_bar": metrics(all_previous),
    }
    ar_mean, ar_lo, ar_hi = bootstrap_mean_ci(
        all_delta_random, iterations=bootstrap_iterations, seed=seed + 101
    )
    ap_mean, ap_lo, ap_hi = bootstrap_mean_ci(
        all_delta_previous, iterations=bootstrap_iterations, seed=seed + 103
    )

    print()
    print("Walk-forward aggregate OOS:")
    print(
        f"Ramon meanR={_fmt(aggregate['ramon']['mean_r'],4)} "
        f"PF={_fmt(aggregate['ramon']['pf_r'],3)} | "
        f"Random meanR={_fmt(aggregate['random']['mean_r'],4)} "
        f"PF={_fmt(aggregate['random']['pf_r'],3)} | "
        f"PrevBar meanR={_fmt(aggregate['previous_bar']['mean_r'],4)} "
        f"PF={_fmt(aggregate['previous_bar']['pf_r'],3)}"
    )
    print(
        f"Ramon - Random mean ΔR={_fmt(ar_mean,4)} "
        f"95% bootstrap CI=[{_fmt(ar_lo,4)}, {_fmt(ar_hi,4)}] | "
        f"positive folds={positive_vs_random}/{len(wf)}"
    )
    print(
        f"Ramon - PreviousBar mean ΔR={_fmt(ap_mean,4)} "
        f"95% bootstrap CI=[{_fmt(ap_lo,4)}, {_fmt(ap_hi,4)}] | "
        f"positive folds={positive_vs_previous}/{len(wf)}"
    )
    print(
        "CI note         : if a Ramon-minus-baseline CI includes 0, this benchmark "
        "does not show a clear difference at the stated confidence level."
    )
    random_means, ramon_mean, percentile = multi_seed_random_distribution(
        [sample for fold in wf for sample in fold.test],
        bars,
        max_bars=max_bars,
        seeds=random_seeds,
        seed_base=seed,
    )
    if random_means:
        random_means_sorted = sorted(random_means)
        lo = random_means_sorted[max(0, int(0.025 * len(random_means_sorted)))]
        hi = random_means_sorted[min(len(random_means_sorted) - 1, int(0.975 * len(random_means_sorted)))]
        print(
            f"Multi-seed random baseline: seeds={random_seeds} | "
            f"random meanR 95% range=[{_fmt(lo,4)}, {_fmt(hi,4)}] | "
            f"Ramon meanR={_fmt(ramon_mean,4)} | Ramon percentile={_fmt(percentile,1)}%"
        )
    amb, total, amb_pct = ambiguous_fraction(
        [sample for fold in wf for sample in fold.test],
        bars,
        direction_mode="ramon",
        max_bars=max_bars,
        seed=seed,
    )
    print(
        f"Ambiguous same-bar Ramon paths: {amb}/{total} "
        f"({_fmt(amb_pct,2)}%) | main result resolves these SL-first"
    )
    print()


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


def run(
    db: str | Path,
    symbol: str,
    *,
    max_bars: int,
    holdout: float,
    seed: int,
    folds: int,
    embargo_bars: int,
    bootstrap_iterations: int,
    random_seeds: int,
) -> None:
    samples, bars = load_data(db, symbol)
    train, test = split_holdout(samples, holdout)
    print("=== RAMON VALIDATION LAB / BASELINE BENCHMARK ===")
    print(f"Symbol          : {symbol}")
    print(f"Executed entries: {len(samples)}")
    print(f"Holdout fraction: {holdout:.0%}")
    print(f"Replay horizon  : {max_bars} M15 bars")
    print("Scope           : real executed Ramon entries only")
    print("Replay start    : first complete M15 bar after actual fill")
    fill_count = sum(sample.actual_fill_price is not None for sample in samples)
    print("Time basis      : UTC canonical for split/purge; broker clock only for CopyRates bar lookup")
    print(f"Actual fill     : {fill_count}/{len(samples)} stored (telemetry/audit; baseline entry geometry unchanged)")
    print("Costs           : entry spread + per-bar spread_points for Ask-aware SELL replay")
    print("Exit model      : BUY uses stored Bid-like OHLC; SELL uses Ask proxy; same-bar ambiguity = SL-first")
    print("Interpretation  : research benchmark only; not live execution logic")
    print()
    print_table("=== FULL HISTORY ===", evaluate(samples, bars, max_bars=max_bars, random_seed=seed))
    print_table("=== EARLY / DEVELOPMENT WINDOW ===", evaluate(train, bars, max_bars=max_bars, random_seed=seed))
    print_table("=== FINAL TIME HOLDOUT (OUT-OF-SAMPLE) ===", evaluate(test, bars, max_bars=max_bars, random_seed=seed))
    print_walk_forward(
        samples,
        bars,
        folds=folds,
        max_bars=max_bars,
        embargo_bars=embargo_bars,
        seed=seed,
        bootstrap_iterations=bootstrap_iterations,
        random_seeds=random_seeds,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Ramon no-skill/simple baseline validation lab")
    parser.add_argument("--db", default="/data/ramon_history.sqlite3")
    parser.add_argument("--symbol", default="XAUUSD_l")
    parser.add_argument("--max-bars", type=int, default=24)
    parser.add_argument("--holdout", type=float, default=0.30)
    parser.add_argument("--seed", type=int, default=26092212)
    parser.add_argument("--folds", type=int, default=5)
    parser.add_argument("--embargo-bars", type=int, default=4)
    parser.add_argument("--bootstrap-iterations", type=int, default=4000)
    parser.add_argument("--random-seeds", type=int, default=1000)
    args = parser.parse_args()
    if args.max_bars < 1:
        parser.error("--max-bars must be >= 1")
    if not 0.10 <= args.holdout <= 0.50:
        parser.error("--holdout must be between 0.10 and 0.50")
    if args.folds < 2:
        parser.error("--folds must be >= 2")
    if args.embargo_bars < 0:
        parser.error("--embargo-bars must be >= 0")
    if args.bootstrap_iterations < 200:
        parser.error("--bootstrap-iterations must be >= 200")
    if args.random_seeds < 100:
        parser.error("--random-seeds must be >= 100")
    run(
        args.db,
        args.symbol,
        max_bars=args.max_bars,
        holdout=args.holdout,
        seed=args.seed,
        folds=args.folds,
        embargo_bars=args.embargo_bars,
        bootstrap_iterations=args.bootstrap_iterations,
        random_seeds=args.random_seeds,
    )


if __name__ == "__main__":
    main()
