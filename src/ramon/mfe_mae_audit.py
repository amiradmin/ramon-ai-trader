"""Read-only MFE/MAE audit for realized Ramon trades.

The audit reconstructs each trade's observed M15 path using persisted history bars.
It never changes labels, models, settings, or live trading state.
"""
from __future__ import annotations

import argparse
import json
import math
import sqlite3
from pathlib import Path
from statistics import median


def _finite(value) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _first_touch(rows, *, direction: str, entry: float, distance: float, favorable: bool):
    if distance <= 0:
        return None
    if direction == "BUY":
        level = entry + distance if favorable else entry - distance
        for t, high, low in rows:
            if (high >= level) if favorable else (low <= level):
                return int(t)
    else:
        level = entry - distance if favorable else entry + distance
        for t, high, low in rows:
            if (low <= level) if favorable else (high >= level):
                return int(t)
    return None


def _path_metrics(rows, *, direction: str, entry: float, stop_distance: float) -> dict:
    if not rows or stop_distance <= 0:
        raise ValueError("rows and positive stop_distance required")
    if direction == "BUY":
        mfe_price = max(float(r[1]) for r in rows) - entry
        mae_price = entry - min(float(r[2]) for r in rows)
    elif direction == "SELL":
        mfe_price = entry - min(float(r[2]) for r in rows)
        mae_price = max(float(r[1]) for r in rows) - entry
    else:
        raise ValueError("direction must be BUY or SELL")
    mfe_price = max(0.0, mfe_price)
    mae_price = max(0.0, mae_price)
    first_fav_05 = _first_touch(rows, direction=direction, entry=entry,
                                distance=.5 * stop_distance, favorable=True)
    first_adv_05 = _first_touch(rows, direction=direction, entry=entry,
                                distance=.5 * stop_distance, favorable=False)
    first_fav_10 = _first_touch(rows, direction=direction, entry=entry,
                                distance=stop_distance, favorable=True)
    first_adv_10 = _first_touch(rows, direction=direction, entry=entry,
                                distance=stop_distance, favorable=False)
    if first_fav_05 is None and first_adv_05 is None:
        sequence = "NEITHER_0_5R"
    elif first_fav_05 is None:
        sequence = "ADVERSE_FIRST"
    elif first_adv_05 is None:
        sequence = "FAVORABLE_FIRST"
    elif first_fav_05 < first_adv_05:
        sequence = "FAVORABLE_FIRST"
    elif first_adv_05 < first_fav_05:
        sequence = "ADVERSE_FIRST"
    else:
        sequence = "SAME_M15_BAR_AMBIGUOUS"
    return {
        "mfe_price": mfe_price,
        "mae_price": mae_price,
        "mfe_r": mfe_price / stop_distance,
        "mae_r": mae_price / stop_distance,
        "first_favorable_0_5r": first_fav_05,
        "first_adverse_0_5r": first_adv_05,
        "first_favorable_1r": first_fav_10,
        "first_adverse_1r": first_adv_10,
        "sequence_0_5r": sequence,
    }


def _classify(row: dict) -> str:
    mfe = row["mfe_r"]
    mae = row["mae_r"]
    net = row["net_r"]
    seq = row["sequence_0_5r"]
    if mfe < .25 and mae >= .5:
        return "DIRECTION_WEAK"
    if seq in {"ADVERSE_FIRST", "SAME_M15_BAR_AMBIGUOUS"} and mfe >= .5:
        return "TIMING_STRESS"
    if net <= 0 and mfe >= .5:
        return "EXIT_LEFT_EDGE"
    if net > 0 and mfe >= 1.0 and net < .5:
        return "PROFIT_GIVEBACK"
    return "MIXED_OR_OK"


def load_audit_rows(con: sqlite3.Connection, symbol: str) -> tuple[list[dict], dict]:
    con.row_factory = sqlite3.Row
    trades = con.execute(
        """SELECT t.trade_key,t.sample_key,t.symbol,t.direction,t.opened,t.closed,
                  t.net_units,t.net_r,t.exit_reason,t.actual_fill_price,
                  s.mid,s.stop_distance,s.target_distance,s.atr
           FROM trade_outcomes t
           LEFT JOIN decision_samples s ON s.sample_key=t.sample_key
           WHERE t.symbol=?
           ORDER BY t.opened""",
        (symbol,),
    ).fetchall()
    rows: list[dict] = []
    skipped = {"missing_entry": 0, "missing_stop": 0, "missing_bars": 0}
    for trade in trades:
        entry = trade["actual_fill_price"] if _finite(trade["actual_fill_price"]) else trade["mid"]
        stop = trade["stop_distance"]
        if not _finite(entry):
            skipped["missing_entry"] += 1
            continue
        if not _finite(stop) or float(stop) <= 0:
            skipped["missing_stop"] += 1
            continue
        bars = con.execute(
            """SELECT time,high,low FROM history_bars
               WHERE symbol=? AND timeframe='M15' AND time>=? AND time<=?
               ORDER BY time""",
            (symbol, int(trade["opened"] // 900 * 900), int(trade["closed"])),
        ).fetchall()
        if not bars:
            skipped["missing_bars"] += 1
            continue
        metrics = _path_metrics(
            [(int(x[0]), float(x[1]), float(x[2])) for x in bars],
            direction=str(trade["direction"]),
            entry=float(entry),
            stop_distance=float(stop),
        )
        item = {
            "trade_key": trade["trade_key"],
            "sample_key": trade["sample_key"],
            "direction": trade["direction"],
            "opened": int(trade["opened"]),
            "closed": int(trade["closed"]),
            "net_units": float(trade["net_units"]),
            "net_r": float(trade["net_r"]),
            "exit_reason": trade["exit_reason"],
            "entry_price": float(entry),
            "entry_price_source": "actual_fill_price" if _finite(trade["actual_fill_price"]) else "decision_mid",
            "stop_distance": float(stop),
            "target_distance": float(trade["target_distance"]) if _finite(trade["target_distance"]) else None,
            "atr": float(trade["atr"]) if _finite(trade["atr"]) else None,
            "bars_observed": len(bars),
            **metrics,
        }
        item["diagnostic_bucket"] = _classify(item)
        rows.append(item)
    return rows, {"total_trades": len(trades), **skipped}


def _rate(count: int, n: int):
    return count / n if n else None


def summarize(rows: list[dict], coverage: dict) -> dict:
    n = len(rows)
    buckets = {}
    sequences = {}
    for row in rows:
        buckets[row["diagnostic_bucket"]] = buckets.get(row["diagnostic_bucket"], 0) + 1
        sequences[row["sequence_0_5r"]] = sequences.get(row["sequence_0_5r"], 0) + 1
    wins = [r for r in rows if r["net_r"] > 0]
    losses = [r for r in rows if r["net_r"] < 0]
    return {
        "status": "evaluated" if rows else "insufficient_coverage",
        "coverage": {**coverage, "audited": n, "coverage_rate": _rate(n, coverage["total_trades"])},
        "performance": {
            "wins": len(wins),
            "losses": len(losses),
            "net_r_sum": sum(r["net_r"] for r in rows),
            "median_net_r": median([r["net_r"] for r in rows]) if rows else None,
        },
        "excursion": {
            "median_mfe_r": median([r["mfe_r"] for r in rows]) if rows else None,
            "median_mae_r": median([r["mae_r"] for r in rows]) if rows else None,
            "losses_with_mfe_ge_0_5r": sum(r["net_r"] <= 0 and r["mfe_r"] >= .5 for r in rows),
            "losses_with_mfe_ge_1r": sum(r["net_r"] <= 0 and r["mfe_r"] >= 1 for r in rows),
            "trades_with_mae_ge_0_5r": sum(r["mae_r"] >= .5 for r in rows),
            "trades_with_mae_ge_1r": sum(r["mae_r"] >= 1 for r in rows),
        },
        "sequence_0_5r": sequences,
        "diagnostic_buckets": buckets,
        "interpretation_guardrail": (
            "M15 bars cannot prove intrabar ordering when favorable and adverse thresholds "
            "are touched in the same candle. Buckets are diagnostics, not causal labels."
        ),
    }


def run(db: str | Path, symbol: str = "XAUUSD_l") -> dict:
    path = Path(db).expanduser().resolve()
    uri = path.as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as con:
        rows, coverage = load_audit_rows(con, symbol)
    return {
        "mode": "READ_ONLY_AUDIT",
        "symbol": symbol,
        "summary": summarize(rows, coverage),
        "trades": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="data/ramon_history.sqlite3")
    parser.add_argument("--symbol", default="XAUUSD_l")
    parser.add_argument("--output", default="data/mfe_mae_audit.json")
    args = parser.parse_args()
    report = run(args.db, args.symbol)
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
