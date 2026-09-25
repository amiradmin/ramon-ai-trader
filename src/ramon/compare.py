"""Read-only, chronological comparison of Chronos with a fixed momentum baseline."""
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import json
from math import sqrt
from pathlib import Path
import sqlite3
from statistics import mean, pstdev
from typing import Sequence

from .core import Forecast, Settings
from .history import load_bars
from .model import ChronosForecaster, model_name
from .replay import replay


class MomentumBaseline:
    """Fixed four-return drift; no fitting or information from future bars."""

    def forecast(self, closes: Sequence[float], horizon: int) -> Forecast:
        if len(closes) < 16 or horizon < 1:
            raise ValueError("need >=16 historical closes and a positive horizon")
        changes = [b - a for a, b in zip(closes[-16:-1], closes[-15:])]
        drift = mean(changes[-4:])
        width = max(pstdev(changes) * sqrt(horizon) * 1.28, 1e-6)
        path = tuple(closes[-1] + drift * n for n in range(1, horizon + 1))
        return Forecast(max(1e-6, path[-1] - width), path[-1], path[-1] + width, path)


def observed_cost_r(db: str | Path, symbol: str) -> tuple[float | None, int]:
    """Mean observed broker fees per initial R; profit already includes bid/ask spread."""
    with sqlite3.connect(db) as conn:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='trade_outcomes'").fetchone():
            return None, 0
        rows = conn.execute("""
            SELECT commission_units,swap_units,fee_units,initial_risk_units
            FROM trade_outcomes
            WHERE symbol=? AND commission_units IS NOT NULL AND swap_units IS NOT NULL
              AND fee_units IS NOT NULL AND initial_risk_units>0
        """, (symbol,)).fetchall()
    if not rows:
        return None, 0
    return mean(max(0.0, -(commission + swap + fee) / risk)
                for commission, swap, fee, risk in rows), len(rows)


def compare(db: str | Path, chronos, *, symbol: str = "XAUUSD_l",
            point: float = 0.01, stride: int = 4,
            cost_r: float | None = None) -> dict[str, object]:
    bars, spreads = load_bars(db, symbol)
    if len(bars) < 600:
        raise ValueError("need >=600 completed bars for the chronological holdout")
    if cost_r is not None and cost_r < 0:
        raise ValueError("cost_r must be nonnegative")
    start = max(256, len(bars) * 4 // 5)
    missing = sum(value <= 0 for value in spreads[start:])
    if missing:
        raise ValueError(f"holdout has {missing} bars without recorded spread; import actual MT5 spreads")
    if cost_r is None:
        cost_r, observed = observed_cost_r(db, symbol)
        cost_source = f"observed broker fees on {observed} closed trades" if observed else "unavailable"
    else:
        cost_source = "user supplied R per round trip"
    # The holdout boundary and all settings are identical. Positions evolve separately;
    # therefore the two models can trade at different timestamps.
    settings = Settings()
    kwargs = dict(point=point, settings=settings, start=start, stride=stride,
                  require_recorded_spreads=True, roundtrip_cost_r=cost_r or 0.0)
    baseline_result = replay(bars, spreads, MomentumBaseline(), **kwargs)
    chronos_result = replay(bars, spreads, chronos, **kwargs)
    results = {"momentum_baseline": asdict(baseline_result), "chronos": asdict(chronos_result)}
    trade_counts = {name: result["buys"] + result["sells"] for name, result in results.items()}
    return {
        "symbol": symbol,
        "holdout": {"start_utc": datetime.fromtimestamp(bars[start].time, timezone.utc).isoformat(),
                    "end_utc": datetime.fromtimestamp(bars[-1].time, timezone.utc).isoformat(),
                    "bars": len(bars) - start, "recorded_spreads": len(bars) - start,
                    "stride": stride},
        "cost": {"roundtrip_r": cost_r, "source": cost_source,
                 "metric": "net R after recorded spread and estimated broker fees" if cost_r is not None
                 else "R after recorded spread only; broker fees unavailable"},
        "results": results,
        "interpretation": (
            "insufficient traded samples for a performance comparison"
            if min(trade_counts.values()) < 20 else
            "descriptive holdout results only; test additional periods before drawing conclusions"
        ),
        "limitations": [
            "Closed M15 OHLC cannot reconstruct intrabar fill order, gaps, slippage or live EA exits.",
            "Both forecasts use completed bars and simulated next-bar entries; this is not a live PnL comparison.",
            "The last 20% is a chronological holdout, but no strategy parameters were fitted here.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare Chronos and fixed momentum on a recorded-spread holdout")
    parser.add_argument("--db", required=True)
    parser.add_argument("--symbol", default="XAUUSD_l")
    parser.add_argument("--model", default="autogluon/chronos-2-small")
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--point", type=float, default=0.01)
    parser.add_argument("--stride", type=int, default=4)
    parser.add_argument("--cost-r", type=float, default=None,
                        help="Explicit estimated broker fees per round trip in initial R; otherwise infer from closed trades")
    args = parser.parse_args()
    # Validate data and spread coverage before the potentially expensive model load.
    bars, spreads = load_bars(args.db, args.symbol)
    if len(bars) < 600:
        parser.error(f"found {len(bars)} completed {args.symbol}/M15 bars; need at least 600. "
                     "Export/import MT5 closed M15 history with spread_points.")
    start = max(256, len(bars) * 4 // 5)
    missing = sum(v <= 0 for v in spreads[start:])
    if missing:
        parser.error(f"found {missing} holdout bars without recorded spread "
                     f"(out of {len(bars) - start}); export/import MT5 closed M15 history "
                     "with spread_points. No assumed spread is used for this comparison.")
    model = ChronosForecaster(model_name(args.model), args.device)
    report = compare(args.db, model, symbol=args.symbol, point=args.point,
                     stride=args.stride, cost_r=args.cost_r)
    report["chronos_model"] = args.model
    report["chronos_revision"] = model.revision
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
