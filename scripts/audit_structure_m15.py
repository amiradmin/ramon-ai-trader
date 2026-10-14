#!/usr/bin/env python3
"""Read-only M15 structure opportunity eligibility audit (NOT P&L backtest).

Uses completed historical M15 bars. Reconstructed bid/ask use hypothetical
close +/- assumed spread; these are NOT historical executable quotes.
"""
import argparse
import collections
import sqlite3
from pathlib import Path

from ramon.core import Bar, Market, atr14
from ramon.market_opportunity_engine import detect_opportunity


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/ramon_history.sqlite3")
    ap.add_argument("--symbol", default="XAUUSD_l")
    ap.add_argument("--spread", type=float, default=0.42,
                    help="Assumed price spread, NOT real quote history")
    ap.add_argument("--lookback", type=int, default=2500)
    args = ap.parse_args()
    if args.spread <= 0:
        ap.error("spread must be positive")
    uri = f"file:{Path(args.db).resolve()}?mode=ro"
    con = sqlite3.connect(uri, uri=True)
    try:
        columns = {row[1] for row in con.execute("PRAGMA table_info(history_bars)")}
        required = {"symbol", "timeframe", "time", "open", "high", "low", "close"}
        if not required.issubset(columns):
            raise RuntimeError(f"history_bars missing columns: {required - columns}")
        # DESC LIMIT followed by chronological sort: never scan the whole database.
        rows = list(con.execute("""
           SELECT time, open, high, low, close FROM (
             SELECT time, open, high, low, close
             FROM history_bars WHERE symbol=? AND timeframe='M15'
             ORDER BY time DESC LIMIT ?
           ) ORDER BY time ASC
        """, (args.symbol, max(128, args.lookback))))
    finally:
        con.close()
    bars = [Bar(int(t), float(o), float(h), float(l), float(c)) for t,o,h,l,c in rows]
    counts = collections.Counter()
    prefilters = collections.Counter()
    samples = []
    candidates = []
    for end in range(128, len(bars)+1):
        b = bars[max(0,end-256):end]
        if len(b) < 128: continue
        mid = b[-1].close
        market = Market(args.symbol, "M15", mid-args.spread/2,
                        mid+args.spread/2, 0.01, tuple(b))
        try:
            result = detect_opportunity(market)
        except ValueError:
            counts["invalid_historical_bars"] += 1
            continue
        counts[result.reason if result.decision=="WAIT" else result.setup] += 1
        # Explain the no_valid_setup funnel without changing production rules.
        if result.reason == "no_valid_setup":
            atr = atr14(b)
            previous = b[-21:-1]
            early = b[-35:-21]
            hi, lo = max(x.high for x in previous), min(x.low for x in previous)
            middle = (hi + lo) / 2
            older_middle = (max(x.high for x in early) + min(x.low for x in early)) / 2
            trend = (b[-1].close - b[-5].close) / atr
            shift = (middle - older_middle) / atr
            if abs(trend) < 0.28:
                prefilters["trend_momentum_below_threshold"] += 1
            if abs(shift) < 0.12:
                prefilters["range_midpoint_shift_small"] += 1
            if (hi-lo) > 3.8*atr:
                prefilters["range_width_too_wide"] += 1
            if (hi-lo) <= 3.8*atr and not (
                (mid <= lo+0.55*atr and b[-1].close>b[-1].open) or
                (mid >= hi-0.55*atr and b[-1].close<b[-1].open)):
                prefilters["range_boundary_rejection_absent"] += 1

        if result.decision != "WAIT":
            candidates.append((b[-1].time, result.decision, result.setup,
                               round(result.reward_risk,3)))
        if len(samples) < 15 or result.decision != "WAIT" and len(samples) < 30:
            samples.append((b[-1].time, result.decision, result.setup, result.reason))
    print(f"symbol={args.symbol} M15_bars={len(bars)} windows={sum(counts.values())} spread_assumption={args.spread:.2f}")
    print("These numbers are setup eligibility diagnostics, NOT trading returns.")
    for reason,n in counts.most_common():
        print(f"{reason}: {n} ({n/max(1,sum(counts.values())):.1%})")
    print("No-setup conditions (may overlap):")
    for reason,n in prefilters.most_common(): print(f"{reason}: {n}")
    print("Sample candidates (bar UTC timestamp, side, setup, theoretical RR):")
    for row in candidates[:20]: print(*row)
    if not candidates: print("(none at the assumed spread)")
    print("Sample windows (bar timestamp, decision, setup, reason):")
    for row in samples[:15]: print(*row)


if __name__ == "__main__":
    main()
