"""Read-only, descriptive audit of the quantile support score on executed trades.

The cohort is selected by the old execution policy. It cannot estimate the
payoff of opportunities that were never executed, or prove a causal improvement.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import sqlite3

from .core import Forecast, Market, forecast_direction_support


@dataclass(frozen=True)
class ScoredTrade:
    support: float
    net_r: float
    time: int


def summarize(rows: list[ScoredTrade]) -> dict[str, float | int | None]:
    return {
        "trades": len(rows),
        "wins": sum(row.net_r > 0 for row in rows),
        "win_rate": sum(row.net_r > 0 for row in rows) / len(rows) if rows else None,
        "net_r": round(sum(row.net_r for row in rows), 4),
    }


def audit(db: str | Path, *, threshold: float = 0.65, point: float = 0.01) -> dict:
    if not 0.0 <= threshold <= 0.9 or point <= 0:
        raise ValueError("invalid threshold or point")
    uri = Path(db).resolve().as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as conn:
        rows = conn.execute("""
            SELECT s.quote_time,s.mid,s.spread,s.model_metadata,t.direction,t.net_r
            FROM decision_samples s JOIN trade_outcomes t ON t.sample_key=s.sample_key
            WHERE t.training_status='LEARNABLE' AND s.model_metadata IS NOT NULL
            ORDER BY s.quote_time,s.sample_key
        """).fetchall()

    scored: list[ScoredTrade] = []
    for time, mid, spread, raw, side, net_r in rows:
        try:
            base = json.loads(raw)["decision_audit"]["base"]
            forecast = Forecast(*(float(base[key]) for key in
                                  ("forecast_low", "forecast_median", "forecast_high")))
            if (side not in {"BUY", "SELL"} or forecast.low <= 0
                    or not forecast.low <= forecast.median <= forecast.high):
                continue
            bid = float(base.get("signal_bid", float(mid) - float(spread) / 2))
            ask = float(base.get("signal_ask", float(mid) + float(spread) / 2))
            market = Market("", "M15", bid, ask, point, ())
            scored.append(ScoredTrade(forecast_direction_support(forecast, market, side),
                                      float(net_r), int(time)))
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            continue

    kept = [row for row in scored if row.support >= threshold]
    removed = [row for row in scored if row.support < threshold]
    return {
        "threshold": threshold,
        "linked_rows": len(rows),
        "scored_rows": len(scored),
        "all": summarize(scored),
        "kept": summarize(kept),
        "removed": summarize(removed),
        "scope": "observed_executed_clean_trades_only; descriptive, not causal",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True)
    parser.add_argument("--threshold", type=float, default=0.65)
    parser.add_argument("--point", type=float, default=0.01)
    args = parser.parse_args()
    print(json.dumps(audit(args.db, threshold=args.threshold, point=args.point), indent=2))


if __name__ == "__main__":
    main()
