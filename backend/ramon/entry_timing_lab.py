from __future__ import annotations

import argparse
import json
import math
import random
import sqlite3
import statistics
from dataclasses import dataclass
from pathlib import Path

from .validation_lab import Result, Sample, metrics, replay_direction


@dataclass(frozen=True)
class TimingRow:
    signal_bar_time: int
    captured: int
    quote_time: int
    direction: str
    mid: float
    spread: float
    atr: float
    stop_distance: float
    target_distance: float
    point: float
    selected: bool
    future_move_atr: float


def _chronos_direction(raw: str | None) -> str | None:
    """Recover Chronos' dominant BUY/SELL side from the stored base edges."""
    if not raw:
        return None
    try:
        data = json.loads(raw)
        base = data["decision_audit"]["base"]
        buy_edge = float(base["buy_edge"])
        sell_edge = float(base["sell_edge"])
    except (json.JSONDecodeError, TypeError, KeyError, ValueError):
        return None
    if not (math.isfinite(buy_edge) and math.isfinite(sell_edge)):
        return None
    return "BUY" if buy_edge >= sell_edge else "SELL"


def load_rows(
    db: str | Path,
    symbol: str,
    *,
    horizon: int,
) -> tuple[list[TimingRow], int]:
    """Load one immutable first snapshot per completed M15 signal bar.

    A bar is marked selected when any decision snapshot on that same signal bar
    is linked to a real executed trade. This measures bar-level entry timing,
    not the exact 30-second snapshot that finally opened the position.
    """
    path = Path(db).expanduser()
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    try:
        bars = list(
            con.execute(
                """SELECT time,close,spread_points
                   FROM history_bars
                   WHERE symbol=? AND timeframe='M15'
                   ORDER BY time""",
                (symbol,),
            )
        )
        index_by_time = {int(row["time"]): index for index, row in enumerate(bars)}

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

        candidates = list(
            con.execute(
                """SELECT s.signal_bar_time,s.captured,s.quote_time,s.mid,s.spread,
                          s.atr,s.stop_distance,s.target_distance,s.model_metadata,
                          EXISTS(
                              SELECT 1
                              FROM decision_samples sx
                              JOIN trade_outcomes t
                                ON t.sample_key=sx.sample_key
                               AND t.symbol=sx.symbol
                              WHERE sx.symbol=s.symbol
                                AND sx.signal_bar_time=s.signal_bar_time
                          ) AS selected
                   FROM decision_samples s
                   JOIN (
                       SELECT signal_bar_time,MIN(captured) AS first_captured
                       FROM decision_samples
                       WHERE symbol=?
                       GROUP BY signal_bar_time
                   ) first
                     ON first.signal_bar_time=s.signal_bar_time
                    AND first.first_captured=s.captured
                   WHERE s.symbol=?
                     AND s.atr>0
                     AND COALESCE(s.stop_distance,0)>0
                     AND COALESCE(s.target_distance,0)>0
                     AND s.model_metadata IS NOT NULL
                   ORDER BY s.signal_bar_time""",
                (symbol, symbol),
            )
        )

        rows: list[TimingRow] = []
        for item in candidates:
            direction = _chronos_direction(item["model_metadata"])
            if direction is None:
                continue
            signal_time = int(item["signal_bar_time"])
            bar_index = index_by_time.get(signal_time)
            if bar_index is None or bar_index + horizon >= len(bars):
                continue
            current_close = float(bars[bar_index]["close"])
            target_close = float(bars[bar_index + horizon]["close"])
            atr = float(item["atr"])
            if not all(
                math.isfinite(v) and v > 0
                for v in (current_close, target_close, atr)
            ):
                continue
            signed = (
                target_close - current_close
                if direction == "BUY"
                else current_close - target_close
            )
            quote_time = (
                int(item["quote_time"])
                if item["quote_time"] is not None
                else signal_time + 900
            )
            rows.append(
                TimingRow(
                    signal_bar_time=signal_time,
                    captured=int(item["captured"]),
                    quote_time=quote_time,
                    direction=direction,
                    mid=float(item["mid"]),
                    spread=float(item["spread"]),
                    atr=atr,
                    stop_distance=float(item["stop_distance"]),
                    target_distance=float(item["target_distance"]),
                    point=point,
                    selected=bool(item["selected"]),
                    future_move_atr=signed / atr,
                )
            )
        return rows, len(candidates)
    finally:
        con.close()


def as_sample(row: TimingRow) -> Sample:
    return Sample(
        captured=row.captured,
        signal_bar_time=row.signal_bar_time,
        entry_time=row.quote_time,
        entry_time_utc=row.captured,
        direction=row.direction,
        mid=row.mid,
        spread=row.spread,
        stop_distance=row.stop_distance,
        target_distance=row.target_distance,
        point=row.point,
    )


def replay_rows(
    rows: list[TimingRow],
    bars: list[sqlite3.Row],
    *,
    max_bars: int,
) -> list[Result]:
    out: list[Result] = []
    for row in rows:
        result = replay_direction(
            bars,
            as_sample(row),
            row.direction,
            max_bars=max_bars,
        )
        if result is not None:
            out.append(result)
    return out


def load_bars(db: str | Path, symbol: str) -> list[sqlite3.Row]:
    con = sqlite3.connect(Path(db).expanduser())
    con.row_factory = sqlite3.Row
    try:
        return list(
            con.execute(
                """SELECT time,open,high,low,close,spread_points
                   FROM history_bars
                   WHERE symbol=? AND timeframe='M15'
                   ORDER BY time""",
                (symbol,),
            )
        )
    finally:
        con.close()


def direction_accuracy(rows: list[TimingRow]) -> float:
    usable = [row for row in rows if row.future_move_atr != 0]
    if not usable:
        return math.nan
    return 100.0 * sum(row.future_move_atr > 0 for row in usable) / len(usable)


def mean_future_move(rows: list[TimingRow]) -> float:
    if not rows:
        return math.nan
    return statistics.mean(row.future_move_atr for row in rows)


def split_holdout(
    rows: list[TimingRow], fraction: float
) -> tuple[list[TimingRow], list[TimingRow]]:
    ordered = sorted(rows, key=lambda row: row.signal_bar_time)
    if not ordered:
        return [], []
    cut = max(1, min(len(ordered) - 1, int(len(ordered) * (1.0 - fraction))))
    return ordered[:cut], ordered[cut:]


def bootstrap_difference_ci(
    selected: list[float],
    nonselected: list[float],
    *,
    iterations: int,
    seed: int,
) -> tuple[float, float, float]:
    if not selected or not nonselected:
        return math.nan, math.nan, math.nan
    observed = statistics.mean(selected) - statistics.mean(nonselected)
    rng = random.Random(seed)
    values = []
    for _ in range(iterations):
        a = [selected[rng.randrange(len(selected))] for _ in selected]
        b = [nonselected[rng.randrange(len(nonselected))] for _ in nonselected]
        values.append(statistics.mean(a) - statistics.mean(b))
    values.sort()
    lo = values[max(0, int(0.025 * len(values)))]
    hi = values[min(len(values) - 1, int(0.975 * len(values)))]
    return observed, lo, hi


def _fmt(value: float, digits: int = 3) -> str:
    if math.isnan(value):
        return "N/A"
    if math.isinf(value):
        return "inf"
    return f"{value:.{digits}f}"


def summarize_group(
    name: str,
    rows: list[TimingRow],
    bars: list[sqlite3.Row],
    *,
    max_bars: int,
) -> dict[str, float]:
    replay = replay_rows(rows, bars, max_bars=max_bars)
    m = metrics(replay)
    return {
        "name": name,
        "bars": float(len(rows)),
        "dir_acc": direction_accuracy(rows),
        "future_move_atr": mean_future_move(rows),
        "trades": m["trades"],
        "win_rate": m["win_rate"],
        "mean_r": m["mean_r"],
        "pf_r": m["pf_r"],
    }


def print_table(
    title: str,
    rows: list[TimingRow],
    bars: list[sqlite3.Row],
    *,
    max_bars: int,
    bootstrap_iterations: int,
    seed: int,
) -> None:
    selected = [row for row in rows if row.selected]
    nonselected = [row for row in rows if not row.selected]
    groups = [
        summarize_group("all", rows, bars, max_bars=max_bars),
        summarize_group("selected", selected, bars, max_bars=max_bars),
        summarize_group("nonselected", nonselected, bars, max_bars=max_bars),
    ]

    print(title)
    print("group          bars  DirAcc%  FutureATR   replayN   WR%    meanR    PF")
    print("-----------------------------------------------------------------------")
    for group in groups:
        print(
            f"{group['name']:12s} {int(group['bars']):5d} "
            f"{_fmt(group['dir_acc'],2):>8s} "
            f"{_fmt(group['future_move_atr'],4):>10s} "
            f"{int(group['trades']):8d} "
            f"{_fmt(group['win_rate'],2):>6s} "
            f"{_fmt(group['mean_r'],4):>8s} "
            f"{_fmt(group['pf_r'],3):>6s}"
        )

    selected_results = replay_rows(selected, bars, max_bars=max_bars)
    nonselected_results = replay_rows(nonselected, bars, max_bars=max_bars)
    r_mean, r_lo, r_hi = bootstrap_difference_ci(
        [result.outcome_r for result in selected_results],
        [result.outcome_r for result in nonselected_results],
        iterations=bootstrap_iterations,
        seed=seed,
    )
    m_mean, m_lo, m_hi = bootstrap_difference_ci(
        [row.future_move_atr for row in selected],
        [row.future_move_atr for row in nonselected],
        iterations=bootstrap_iterations,
        seed=seed + 17,
    )

    print(
        f"Selected - nonselected meanR     : {_fmt(r_mean,4)} "
        f"95% CI=[{_fmt(r_lo,4)}, {_fmt(r_hi,4)}]"
    )
    print(
        f"Selected - nonselected FutureATR : {_fmt(m_mean,4)} "
        f"95% CI=[{_fmt(m_lo,4)}, {_fmt(m_hi,4)}]"
    )
    print()


def run(
    db: str | Path,
    symbol: str,
    *,
    horizon: int,
    max_bars: int,
    holdout: float,
    bootstrap_iterations: int,
    seed: int,
) -> None:
    rows, candidate_count = load_rows(db, symbol, horizon=horizon)
    bars = load_bars(db, symbol)
    _development, test = split_holdout(rows, holdout)

    selected_count = sum(row.selected for row in rows)
    print("=== RAMON ALL-BARS / ENTRY-TIMING BENCHMARK ===")
    print(f"Symbol                  : {symbol}")
    print(f"Candidate signal bars   : {candidate_count}")
    print(f"Usable Chronos bars     : {len(rows)}")
    print(f"Ramon-selected bars     : {selected_count}/{len(rows)}")
    print(f"Selection rate          : {100.0 * selected_count / len(rows):.2f}%" if rows else "Selection rate          : N/A")
    print(f"Forecast horizon        : {horizon} observed M15 bars")
    print(f"Replay horizon          : {max_bars} M15 bars")
    print("Snapshot policy         : first persisted snapshot per signal_bar_time")
    print("Selected definition     : any real executed trade on the same signal bar")
    print("Chronos direction       : dominant stored base edge (BUY vs SELL)")
    print("Entry timing scope      : bar-level selection; not exact 30-second fill snapshot")
    print("Live effect             : NONE (research-only)")
    print()

    print_table(
        "=== FULL OBSERVED SIGNAL-BAR HISTORY ===",
        rows,
        bars,
        max_bars=max_bars,
        bootstrap_iterations=bootstrap_iterations,
        seed=seed,
    )
    print_table(
        "=== FINAL TIME HOLDOUT ===",
        test,
        bars,
        max_bars=max_bars,
        bootstrap_iterations=bootstrap_iterations,
        seed=seed + 101,
    )

    print(
        "Interpretation           : positive selected-minus-nonselected uplift suggests "
        "Ramon's timing/filtering is concentrating Chronos on better bars; a CI crossing "
        "zero is inconclusive."
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Research-only all-bars Chronos entry-timing benchmark"
    )
    parser.add_argument("--db", default="/data/ramon_history.sqlite3")
    parser.add_argument("--symbol", default="XAUUSD_l")
    parser.add_argument("--horizon", type=int, default=4)
    parser.add_argument("--max-bars", type=int, default=24)
    parser.add_argument("--holdout", type=float, default=0.30)
    parser.add_argument("--bootstrap-iterations", type=int, default=4000)
    parser.add_argument("--seed", type=int, default=26092212)
    args = parser.parse_args()

    if args.horizon < 1 or args.horizon > 24:
        parser.error("--horizon must be between 1 and 24")
    if args.max_bars < 1:
        parser.error("--max-bars must be >= 1")
    if not 0.10 <= args.holdout <= 0.50:
        parser.error("--holdout must be between 0.10 and 0.50")
    if args.bootstrap_iterations < 200:
        parser.error("--bootstrap-iterations must be >= 200")

    run(
        args.db,
        args.symbol,
        horizon=args.horizon,
        max_bars=args.max_bars,
        holdout=args.holdout,
        bootstrap_iterations=args.bootstrap_iterations,
        seed=args.seed,
    )


if __name__ == "__main__":
    main()
