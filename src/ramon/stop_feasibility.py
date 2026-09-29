"""Read-only M5 history audit for gold minimum-lot stop feasibility.

This does not replay Ramon decisions. The stop-hit proxy tests both directions
at every eligible M15 bar and must not be used as a profitability estimate.
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from pathlib import Path
from statistics import median


@dataclass(frozen=True)
class Bar:
    time: int
    open: float
    high: float
    low: float
    close: float
    spread_points: int


def load_m15(path: str | Path) -> list[Bar]:
    """Use complete, consecutive M5 triples only; skip incomplete M15 bars."""
    buckets: dict[int, list[Bar]] = {}
    with open(path, newline="", encoding="utf-8-sig") as stream:
        for row in csv.DictReader(stream):
            bar = Bar(
                int(row["time"]), float(row["open"]), float(row["high"]),
                float(row["low"]), float(row["close"]), int(row["spread_points"]),
            )
            if bar.low > min(bar.open, bar.close) or bar.high < max(bar.open, bar.close):
                raise ValueError("invalid OHLC")
            buckets.setdefault(bar.time - bar.time % 900, []).append(bar)
    result = []
    for start, parts in sorted(buckets.items()):
        parts.sort(key=lambda part: part.time)
        if [part.time for part in parts] != [start, start + 300, start + 600]:
            continue
        result.append(Bar(
            start, parts[0].open, max(part.high for part in parts),
            min(part.low for part in parts), parts[-1].close,
            max(part.spread_points for part in parts),
        ))
    return result


def audit(
    bars: list[Bar], *, cap_usd: float, min_lot_usd_per_price: float,
    point: float = 0.01, hold_bars: int = 4,
    factors: tuple[float, ...] = (1.5, 1.25, 1.0),
) -> dict:
    if cap_usd <= 0 or min_lot_usd_per_price <= 0 or point <= 0 or hold_bars < 1:
        raise ValueError("positive risk, point and hold_bars required")
    if not factors or any(f <= 0 for f in factors):
        raise ValueError("positive stop factors required")
    results = {factor: {"eligible": 0, "hits_common": 0} for factor in factors}
    all_atrs: list[float] = []
    common_directions = 0
    for i in range(14, len(bars) - hold_bars - 1):
        # 15 completed bars ending at i; the 14 true ranges match core.atr14.
        window = bars[i - 14:i + 1]
        if any(b.time - a.time != 900 for a, b in zip(window, window[1:])):
            continue
        future = bars[i + 1:i + hold_bars + 1]
        if any(b.time - a.time != 900 for a, b in zip(window[-1:] + future, future)):
            continue
        atr = sum(max(b.high - b.low, abs(b.high - a.close), abs(b.low - a.close))
                  for a, b in zip(window, window[1:])) / 14
        all_atrs.append(atr)
        for factor in factors:
            if factor * atr * min_lot_usd_per_price <= cap_usd:
                results[factor]["eligible"] += 1
        if max(factors) * atr * min_lot_usd_per_price > cap_usd:
            continue
        for direction in ("BUY", "SELL"):
            common_directions += 1
            spread = future[0].spread_points * point
            entry = future[0].open + (spread if direction == "BUY" else 0)
            for factor in factors:
                stop = entry + (-factor if direction == "BUY" else factor) * atr
                hit = any(
                    bar.low <= stop if direction == "BUY"
                    else bar.high + bar.spread_points * point >= stop
                    for bar in future
                )
                results[factor]["hits_common"] += int(hit)
    if not all_atrs:
        raise ValueError("need at least 15 consecutive M15 bars and a future window")
    return {
        "bars_evaluated": len(all_atrs),
        "median_atr": median(all_atrs),
        "common_direction_samples": common_directions,
        "factors": {
            str(factor): {
                "eligible": results[factor]["eligible"],
                "eligible_pct": 100 * results[factor]["eligible"] / len(all_atrs),
                "common_stop_hit_pct": (
                    100 * results[factor]["hits_common"] / common_directions
                    if common_directions else None
                ),
            }
            for factor in factors
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv", help="Exported XAUUSD_l M5 history CSV")
    parser.add_argument("--cap-usd", type=float, default=0.06,
                        help="Preferred risk budget in USD; default Ramon $0.06")
    parser.add_argument("--min-lot-usd-per-price", type=float, required=True,
                        help="Broker minimum-lot USD loss per 1.00 gold price move")
    parser.add_argument("--point", type=float, default=0.01)
    args = parser.parse_args()
    result = audit(load_m15(args.csv), cap_usd=args.cap_usd,
                   min_lot_usd_per_price=args.min_lot_usd_per_price,
                   point=args.point)
    print(f"Complete M15 windows: {result['bars_evaluated']}; median ATR: {result['median_atr']:.2f}")
    print("Stop ATR | Eligible below cap | Stop touched within four bars, same opportunities")
    for factor, stats in result["factors"].items():
        hit = stats["common_stop_hit_pct"]
        print(f"{factor:>8} | {stats['eligible']:>5} ({stats['eligible_pct']:5.1f}%) "
              f"| {hit:.1f}%" if hit is not None else f"{factor}: no common opportunities")
    print("PROXY ONLY: both hypothetical directions at every bar; no Ramon signals, "
          "TP, commissions, slippage or position management. No live setting is changed.")


if __name__ == "__main__":
    main()
