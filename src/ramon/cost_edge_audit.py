"""Read-only audit: how much directional skill does Ramon need to beat its costs?

Research-only. It never touches live decisions, thresholds, sizing, SL/TP or the EA.

For every completed M15 bar it measures the realised absolute move over the
forecast horizon (default 4 bars) and compares it with the round-trip cost
(spread + an assumed extra cost, both in price units). A direction rule with
accuracy ``a`` earns ``(2a - 1) * E|move|`` gross per trade, so the accuracy
needed to break even is::

    breakeven_accuracy = 0.5 + cost / (2 * E|move|)

This is direction-agnostic: it says how good ANY signal must be in each
hour / volatility bucket before costs stop eating the edge. Compare the
result with the measured out-of-sample direction accuracy (the calibration
lab reports ~50-51%). Buckets whose breakeven accuracy is far above that
are structurally untradeable at the current cost level.

Only horizons whose bars are contiguous (no weekend/session gap) are used.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
from collections import defaultdict
from pathlib import Path

BAR_SECONDS = 900


def _load(db: Path, symbol: str):
    with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as conn:
        return conn.execute(
            "SELECT time, high, low, close, spread_points FROM history_bars "
            "WHERE symbol=? AND timeframe='M15' ORDER BY time",
            (symbol,),
        ).fetchall()


def _atr14(rows: list, end: int) -> float | None:
    """Mean of the 14 true ranges ending at index ``end`` (inclusive)."""
    if end < 14:
        return None
    total = 0.0
    for i in range(end - 13, end + 1):
        _, high, low, _, _ = rows[i]
        prev_close = rows[i - 1][3]
        total += max(high - low, abs(high - prev_close), abs(low - prev_close))
    return total / 14.0


def collect(rows: list, *, horizon: int, point: float, fallback_spread_points: float,
            extra_cost_price: float) -> list[dict]:
    samples = []
    for i in range(14, len(rows) - horizon):
        t = rows[i][0]
        # Require a gap-free window (ATR lookback and forecast horizon).
        if rows[i][0] - rows[i - 14][0] != 14 * BAR_SECONDS:
            continue
        if rows[i + horizon][0] - t != horizon * BAR_SECONDS:
            continue
        atr = _atr14(rows, i)
        if not atr or atr <= 0:
            continue
        spread_points = rows[i][4] if rows[i][4] > 0 else fallback_spread_points
        spread = spread_points * point
        move = abs(rows[i + horizon][3] - rows[i][3])
        samples.append({
            "hour": (t // 3600) % 24,
            "atr": atr,
            "move": move,
            "cost": spread + extra_cost_price,
            "spread_known": rows[i][4] > 0,
        })
    return samples


def _summarise(items: list[dict], stop_atr: float) -> dict:
    mean_move = statistics.fmean(s["move"] for s in items)
    mean_cost = statistics.fmean(s["cost"] for s in items)
    mean_stop = stop_atr * statistics.fmean(s["atr"] for s in items)
    breakeven = 0.5 + mean_cost / (2.0 * mean_move) if mean_move > 0 else float("inf")
    return {
        "bars": len(items),
        "mean_abs_move": round(mean_move, 4),
        "mean_cost": round(mean_cost, 4),
        "cost_in_R": round(mean_cost / mean_stop, 4) if mean_stop > 0 else None,
        "move_to_cost": round(mean_move / mean_cost, 2) if mean_cost > 0 else None,
        "breakeven_accuracy": round(min(breakeven, 9.99), 4),
    }


def audit(samples: list[dict], *, stop_atr: float, buckets: int = 4) -> dict:
    if len(samples) < 200:
        return {"status": "insufficient_samples", "bars": len(samples)}
    by_hour: dict[int, list[dict]] = defaultdict(list)
    for s in samples:
        by_hour[s["hour"]].append(s)
    ordered = sorted(samples, key=lambda s: s["atr"] / max(s["cost"], 1e-12))
    size = len(ordered) // buckets
    by_ratio = {}
    for b in range(buckets):
        chunk = ordered[b * size:(b + 1) * size if b < buckets - 1 else len(ordered)]
        by_ratio[f"atr_to_cost_q{b + 1}"] = _summarise(chunk, stop_atr)
    return {
        "status": "ok",
        "overall": _summarise(samples, stop_atr),
        "spread_known_fraction": round(
            sum(s["spread_known"] for s in samples) / len(samples), 4),
        "by_utc_hour": {h: _summarise(v, stop_atr)
                        for h, v in sorted(by_hour.items()) if len(v) >= 30},
        "by_atr_to_cost_quartile": by_ratio,
        "note": ("breakeven_accuracy = 0.5 + cost / (2 * mean|move|). Compare with measured "
                 "out-of-sample direction accuracy; overlapping windows are not independent."),
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--db", required=True)
    p.add_argument("--symbol", default="XAUUSD_l")
    p.add_argument("--horizon", type=int, default=4)
    p.add_argument("--point", type=float, default=0.01)
    p.add_argument("--fallback-spread-points", type=float, default=42.0)
    p.add_argument("--extra-cost-price", type=float, default=0.0,
                   help="assumed additional round-trip cost in price units (e.g. 0.1R * stop)")
    p.add_argument("--stop-atr", type=float, default=1.5)
    p.add_argument("--json-output")
    args = p.parse_args(argv)
    rows = _load(Path(args.db).expanduser(), args.symbol)
    samples = collect(rows, horizon=args.horizon, point=args.point,
                      fallback_spread_points=args.fallback_spread_points,
                      extra_cost_price=args.extra_cost_price)
    report = audit(samples, stop_atr=args.stop_atr)
    text = json.dumps(report, indent=2)
    print(text)
    if args.json_output:
        Path(args.json_output).write_text(text + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
