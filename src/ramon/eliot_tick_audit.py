"""Compare identical Chronos M5 candidates on broker ticks and M5 candle proxy."""

from __future__ import annotations

import argparse
from collections import Counter
import json
import sqlite3

from .eliot_forward import candidate
from .eliot_research import load_m5
from .eliot_roles import HORIZON, outcome
from .eliot_ticks import Tick, check_alignment, tick_outcome


def audit(rows, forecaster, tick_db: str, *, symbol: str,
          units_per_price: float) -> dict:
    with sqlite3.connect(tick_db) as con:
        low, high = con.execute(
            "SELECT MIN(time_msc),MAX(time_msc) FROM eliot_ticks WHERE symbol=?",
            (symbol,),
        ).fetchone()
        if low is None:
            raise ValueError("import tick data before running the audit")
        considered = 0
        compared = 0
        ignored_gaps = 0
        tick_counts: Counter[str] = Counter()
        candle_counts: Counter[str] = Counter()
        tick_pnl = candle_pnl = 0.0
        next_entry = low
        for index in range(63, len(rows) - HORIZON):
            entry_msc = (rows[index].bar.time + 300) * 1000
            end_msc = entry_msc + HORIZON * 300000
            if entry_msc < low or end_msc > high or entry_msc < next_entry:
                continue
            item = candidate(rows, index, forecaster)
            if item is None:
                continue
            considered += 1
            side, _ = item
            raw = con.execute(
                "SELECT time_msc,bid,ask FROM eliot_ticks WHERE symbol=? "
                "AND time_msc >= ? AND time_msc < ? ORDER BY time_msc",
                (symbol, entry_msc, end_msc),
            ).fetchall()
            ticks = [Tick(int(msc), float(bid), float(ask)) for msc, bid, ask in raw]
            if (not ticks or ticks[-1].time_msc < end_msc - 120000
                    or any(b.time_msc - a.time_msc > 120000
                           for a, b in zip(ticks, ticks[1:]))):
                ignored_gaps += 1
                continue
            actual = tick_outcome(ticks, entry_msc=entry_msc, end_msc=end_msc,
                                  side=side, units_per_price=units_per_price)
            if actual is None:
                ignored_gaps += 1
                continue
            proxy = outcome(rows, index, side, units_per_price)
            tick_counts[actual[0]] += 1
            candle_counts[proxy[0]] += 1
            tick_pnl += actual[1]
            candle_pnl += proxy[1]
            compared += 1
            next_entry = end_msc
    return {"candidate_signals": considered, "compared": compared,
            "skipped_missing_tick_coverage": ignored_gaps,
            "tick_outcomes": dict(tick_counts), "candle_outcomes": dict(candle_counts),
            "tick_net_account_units": round(tick_pnl, 3),
            "candle_net_account_units_on_same_signals": round(candle_pnl, 3),
            "limitations": "Historical quote replay, no latency, slippage, commission, or live fills. "
                           "Only intervals with sufficient ticks and matching M5 prices are compared."}


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit Eliot M5 fills against broker bid/ask ticks")
    parser.add_argument("--db", default="/data/ramon_history.sqlite3")
    parser.add_argument("--ticks-db", default="/data/eliot_ticks.sqlite3")
    parser.add_argument("--symbol", default="XAUUSD_l")
    parser.add_argument("--model", default="autogluon/chronos-2-small")
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--units-per-price", type=float, required=True)
    args = parser.parse_args()
    if args.units_per_price <= 0:
        parser.error("--units-per-price must be positive")
    alignment = check_alignment(args.db, args.ticks_db, symbol=args.symbol)
    from .model import ChronosForecaster
    rows = load_m5(args.db, args.symbol, .01)
    result = audit(rows, ChronosForecaster(args.model, args.device), args.ticks_db,
                   symbol=args.symbol, units_per_price=args.units_per_price)
    result["alignment"] = alignment
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
