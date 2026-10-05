"""Read-only volatility-regime analysis for Ramon closed trades.

This module intentionally does not change live execution. It measures whether
Ramon behaves differently across ATR buckets using the exact ATR recorded in
decision_samples at entry, joined to closed trade outcomes.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
import math
import os
from pathlib import Path
import sqlite3
from typing import Iterable


@dataclass(frozen=True)
class BucketStats:
    label: str
    lower: float | None
    upper: float | None
    trades: int
    wins: int
    losses: int
    breakeven: int
    win_rate_pct: float | None
    net_units: float
    avg_net_r: float | None
    profit_factor: float | str | None
    avg_mfe_atr: float | None
    avg_mae_atr: float | None


def parse_bins(text: str) -> tuple[float, ...]:
    values = tuple(float(part.strip()) for part in text.split(",") if part.strip())
    if not values:
        raise ValueError("at least one ATR boundary is required")
    if any(not math.isfinite(v) or v <= 0 for v in values):
        raise ValueError("ATR boundaries must be finite and > 0")
    if tuple(sorted(set(values))) != values:
        raise ValueError("ATR boundaries must be strictly increasing")
    return values


def bucket_label(value: float, boundaries: tuple[float, ...]) -> tuple[str, float | None, float | None]:
    lower = None
    for upper in boundaries:
        if value < upper:
            return (f"<{upper:g}" if lower is None else f"{lower:g}-{upper:g}", lower, upper)
        lower = upper
    return f">={boundaries[-1]:g}", boundaries[-1], None


def _pf(gross_win: float, gross_loss: float) -> float | str | None:
    if gross_loss < 0:
        return gross_win / abs(gross_loss)
    if gross_win > 0:
        return "INF"
    return None


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def load_rows(db: str, symbol: str) -> list[dict]:
    path = Path(db).expanduser().resolve()
    uri = path.as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as con:
        con.row_factory = sqlite3.Row
        sample_cols = {row[1] for row in con.execute("PRAGMA table_info(decision_samples)")}
        trade_cols = {row[1] for row in con.execute("PRAGMA table_info(trade_outcomes)")}
        if not {"sample_key", "symbol", "atr"} <= sample_cols:
            raise ValueError("decision_samples does not contain sample_key/symbol/atr")
        if not {"sample_key", "symbol", "net_units", "net_r"} <= trade_cols:
            raise ValueError("trade_outcomes schema is missing required columns")

        has_targets = con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='target_outcomes'"
        ).fetchone() is not None
        target_join = (
            "LEFT JOIN target_outcomes x ON x.sample_key=t.sample_key AND x.symbol=t.symbol"
            if has_targets else ""
        )
        target_fields = "x.mfe_atr, x.mae_atr" if has_targets else "NULL AS mfe_atr, NULL AS mae_atr"
        status_filter = (
            "AND COALESCE(t.training_status,'LEARNABLE')='LEARNABLE'"
            if "training_status" in trade_cols else ""
        )
        rows = con.execute(
            f"""
            SELECT t.trade_key,t.direction,t.net_units,t.net_r,t.exit_reason,
                   s.atr,{target_fields}
            FROM trade_outcomes t
            JOIN decision_samples s
              ON s.sample_key=t.sample_key AND s.symbol=t.symbol
            {target_join}
            WHERE t.symbol=? {status_filter}
            ORDER BY t.opened,t.trade_key
            """,
            (symbol,),
        ).fetchall()
    return [dict(row) for row in rows]


def summarize(rows: Iterable[dict], boundaries: tuple[float, ...]) -> list[BucketStats]:
    grouped: dict[str, dict] = {}
    order: list[str] = []
    for raw in rows:
        atr = float(raw["atr"])
        if not math.isfinite(atr) or atr <= 0:
            continue
        label, lower, upper = bucket_label(atr, boundaries)
        if label not in grouped:
            grouped[label] = {
                "lower": lower, "upper": upper, "rows": [],
            }
            order.append(label)
        grouped[label]["rows"].append(raw)

    out: list[BucketStats] = []
    for label in order:
        group = grouped[label]
        items = group["rows"]
        net = [float(r["net_units"]) for r in items]
        rs = [float(r["net_r"]) for r in items]
        wins = sum(v > 0 for v in net)
        losses = sum(v < 0 for v in net)
        bes = len(net) - wins - losses
        gross_win = sum(v for v in net if v > 0)
        gross_loss = sum(v for v in net if v < 0)
        mfes = [float(r["mfe_atr"]) for r in items if r.get("mfe_atr") is not None]
        maes = [float(r["mae_atr"]) for r in items if r.get("mae_atr") is not None]
        out.append(BucketStats(
            label=label,
            lower=group["lower"],
            upper=group["upper"],
            trades=len(items),
            wins=wins,
            losses=losses,
            breakeven=bes,
            win_rate_pct=(100.0 * wins / len(items)) if items else None,
            net_units=sum(net),
            avg_net_r=_mean(rs),
            profit_factor=_pf(gross_win, gross_loss),
            avg_mfe_atr=_mean(mfes),
            avg_mae_atr=_mean(maes),
        ))
    return out


def candidate_bucket(stats: list[BucketStats], minimum_trades: int) -> BucketStats | None:
    eligible = [s for s in stats if s.trades >= minimum_trades]
    if not eligible:
        return None
    # Ranking is intentionally simple and descriptive: positive expectancy first,
    # then PF, then sample size. It is NOT a live trading decision.
    def key(s: BucketStats) -> tuple[float, float, int]:
        pf = math.inf if s.profit_factor == "INF" else float(s.profit_factor or 0.0)
        return (float(s.avg_net_r or 0.0), pf, s.trades)
    return max(eligible, key=key)


def report(db: str, symbol: str, boundaries: tuple[float, ...], minimum_trades: int, as_json: bool = False) -> dict:
    rows = load_rows(db, symbol)
    stats = summarize(rows, boundaries)
    candidate = candidate_bucket(stats, minimum_trades)
    payload = {
        "symbol": symbol,
        "source": "closed LEARNABLE trades joined to entry-time decision_samples.atr",
        "boundaries": list(boundaries),
        "minimum_trades_per_bucket": minimum_trades,
        "joined_trades": sum(s.trades for s in stats),
        "buckets": [asdict(s) for s in stats],
        "candidate_bucket": asdict(candidate) if candidate else None,
        "live_execution_changed": False,
        "note": (
            "Descriptive evidence only. Do not relax/tighten live entry or TP rules until "
            "the bucket has enough samples and survives replay/holdout validation."
        ),
    }
    if as_json:
        print(json.dumps(payload, indent=2, ensure_ascii=False, allow_nan=False))
        return payload

    print("=== RAMON VOLATILITY REGIME LAB (READ ONLY) ===")
    print(f"Symbol: {symbol} | joined clean trades: {payload['joined_trades']}")
    print("ATR buckets are based on the exact ATR stored at entry.")
    print()
    print(f"{'ATR':>10} {'N':>5} {'WR%':>7} {'NET':>10} {'AVG R':>9} {'PF':>8} {'MFEa':>8} {'MAEa':>8}")
    for s in stats:
        wr = "-" if s.win_rate_pct is None else f"{s.win_rate_pct:.1f}"
        ar = "-" if s.avg_net_r is None else f"{s.avg_net_r:+.3f}"
        pf = "-" if s.profit_factor is None else str(s.profit_factor) if isinstance(s.profit_factor, str) else f"{s.profit_factor:.2f}"
        mfe = "-" if s.avg_mfe_atr is None else f"{s.avg_mfe_atr:.2f}"
        mae = "-" if s.avg_mae_atr is None else f"{s.avg_mae_atr:.2f}"
        print(f"{s.label:>10} {s.trades:5d} {wr:>7} {s.net_units:+10.2f} {ar:>9} {pf:>8} {mfe:>8} {mae:>8}")
    print()
    if candidate is None:
        print(f"No ATR bucket has >= {minimum_trades} clean trades yet.")
    else:
        print(
            f"Current descriptive candidate: ATR {candidate.label} "
            f"(N={candidate.trades}, avgR={candidate.avg_net_r:+.3f}, PF={candidate.profit_factor})."
        )
    print("LIVE EXECUTION UNCHANGED. Validate a candidate in replay/holdout before making Ramon adaptive.")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=os.getenv("RAMON_HISTORY_DB", "/data/ramon_history.sqlite3"))
    parser.add_argument("--symbol", default=os.getenv("RAMON_SYMBOL", "XAUUSD_l"))
    parser.add_argument("--bins", default="5,8,12", help="comma-separated ATR boundaries")
    parser.add_argument("--minimum-trades", type=int, default=20)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if args.minimum_trades < 1:
        parser.error("--minimum-trades must be >= 1")
    try:
        boundaries = parse_bins(args.bins)
    except ValueError as exc:
        parser.error(str(exc))
    report(args.db, args.symbol, boundaries, args.minimum_trades, as_json=args.json)


if __name__ == "__main__":
    main()
