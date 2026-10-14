#!/usr/bin/env python3
"""Read-only historical M15 opportunity outcome audit. NOT a live execution simulation.

Candidates use only completed bars up to signal bar; ENTRY is hypothetical next
bar OPEN +/- assumed half-spread. SL/TP are shifted by entry slippage from signal
quote. Each candidate is evaluated for the next N completed M15 bars.
If both levels touch in the same candle: AMBIGUOUS, not a win.
No open positions, overlapping-trade restrictions, commission, financing,
slippage, tick sequencing, market fills or EA veto gates are simulated.
"""
import argparse
from collections import Counter, defaultdict
from pathlib import Path
import sqlite3
from ramon.core import Bar, Market
from ramon.market_opportunity_engine import detect_opportunity


def classify(side, entry, stop, target, future):
    for elapsed, bar in enumerate(future, 1):
        sl = bar.low <= stop if side == "BUY" else bar.high >= stop
        tp = bar.high >= target if side == "BUY" else bar.low <= target
        if sl and tp:
            return "AMBIGUOUS", elapsed
        if sl:
            return "SL", elapsed
        if tp:
            return "TP", elapsed
    return "TIMEOUT", len(future)


def audit(bars, *, spread, horizon):
    stats = defaultdict(Counter)
    details = []
    for end in range(128, len(bars)-horizon):
        history = bars[max(0, end-256):end]
        signal_close = history[-1].close
        market = Market("XAUUSD_l", "M15", signal_close-spread/2,
                        signal_close+spread/2, .01, tuple(history))
        try:
            idea = detect_opportunity(market)
        except ValueError:
            continue
        if idea.decision == "WAIT":
            continue
        # Next-bar entry is the earliest possible decision-time fill proxy.
        # Exclude overnight/weekend gaps rather than invent continuous liquidity.
        if bars[end].time - bars[end-1].time != 900:
            stats[idea.setup]["GAP_SKIP"] += 1
            continue
        entry = bars[end].open + (spread/2 if idea.decision=="BUY" else -spread/2)
        delta = entry - idea.entry
        stop = idea.stop + delta
        target = idea.target + delta
        if not (stop < entry < target if idea.decision=="BUY"
                else target < entry < stop):
            stats[idea.setup]["INVALID"] += 1
            continue
        future = bars[end:end+horizon]
        if any(right.time-left.time != 900 for left,right in zip([bars[end-1]]+future[:-1],future)):
            stats[idea.setup]["GAP_SKIP"] += 1
            continue
        label, elapsed = classify(idea.decision, entry, stop, target, future)
        stats[idea.setup][label] += 1
        details.append((bars[end-1].time, idea.setup, idea.decision,
                        label, elapsed, round(idea.reward_risk,3)))
    return stats, details


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--db",default="data/ramon_history.sqlite3")
    p.add_argument("--symbol",default="XAUUSD_l")
    p.add_argument("--spread",type=float,default=0.42)
    p.add_argument("--lookback",type=int,default=2500)
    p.add_argument("--horizon",type=int,default=8)
    a=p.parse_args()
    if a.horizon<1 or a.spread<=0: p.error("horizon must be >=1 and spread positive")
    con=sqlite3.connect(f"file:{Path(a.db).resolve()}?mode=ro",uri=True)
    try:
        rows=con.execute("""SELECT time,open,high,low,close FROM
        (SELECT time,open,high,low,close FROM history_bars
         WHERE symbol=? AND timeframe='M15' ORDER BY time DESC LIMIT ?)
        ORDER BY time""",(a.symbol,max(130,a.lookback))).fetchall()
    finally:
        con.close()
    bars=[Bar(int(t),float(o),float(h),float(l),float(c)) for t,o,h,l,c in rows]
    stats, details=audit(bars,spread=a.spread,horizon=a.horizon)
    print(f"bars={len(bars)} spread_assumed={a.spread} horizon_M15={a.horizon}")
    print("HYPOTHETICAL touch audit; not realized P&L, fills, or validated trading edge.")
    for key in ("TREND_PULLBACK","RANGE_REJECTION"):
        counts=stats[key]
        resolved=counts["TP"]+counts["SL"]
        print(f"{key}: TP={counts['TP']} SL={counts['SL']} AMBIGUOUS={counts['AMBIGUOUS']} "
              f"TIMEOUT={counts['TIMEOUT']} GAP_SKIP={counts['GAP_SKIP']} INVALID={counts['INVALID']} "
              f"resolved_TP_rate={(counts['TP']/resolved if resolved else float('nan')):.1%}")
    print("Rows: signal_utc_epoch setup side outcome bars_elapsed theoretical_RR")
    for line in details:
        print(*line)


if __name__=="__main__":
    main()
