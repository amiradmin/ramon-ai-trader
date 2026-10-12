"""Leakage-conscious offline M1/M5 momentum audit; NO trading or execution hooks.

Uses only bars fully closed by each decision time. Results are observational:
OHLC closes exclude commissions, slippage, and intra-bar stop/target paths.
"""
from __future__ import annotations
import argparse
import json
import sqlite3
from collections import defaultdict
from pathlib import Path
from .scalp_timing import scalp_timing


def evaluate(db: str | Path, symbol: str = "XAUUSD_l",
             horizon_minutes: int = 5, spread_price: float = 0.42) -> dict:
    if not 1 <= horizon_minutes <= 60 or spread_price < 0:
        raise ValueError("invalid horizon or spread")
    con = sqlite3.connect(f"file:{Path(db).resolve()}?mode=ro", uri=True)
    try:
        m1 = con.execute("""SELECT time,close FROM mtf_utc_bars
                            WHERE symbol=? AND timeframe='M1' ORDER BY time""",(symbol,)).fetchall()
        closes = {int(t):float(c) for t,c in m1}
        # Decision on minute boundary uses the prior completed candle.
        times = [int(t)+60 for t,_ in m1]
        report = defaultdict(lambda: {"signals":0,"wins":0,"net_move_sum":0.0})
        eligible = 0
        for at in times:
            entry_t = at-60
            exit_t = at+(horizon_minutes-1)*60
            if entry_t not in closes or exit_t not in closes:
                continue
            eligible += 1
            signal = scalp_timing(con, symbol, now=at)
            if signal["status"] != "AGREE":
                continue
            direction = signal["direction"]
            signed = (closes[exit_t]-closes[entry_t])*(1 if direction=="BUY" else -1)
            net = signed-spread_price
            item = report[direction]
            item["signals"] += 1
            item["wins"] += int(net>0)
            item["net_move_sum"] += net
        summary = {}
        for direction in ("BUY","SELL"):
            r=report[direction]
            n=r["signals"]
            summary[direction] = {"signals":n,"win_rate":r["wins"]/n if n else None,
                                  "mean_net_price_move":r["net_move_sum"]/n if n else None}
        return {"symbol":symbol,"horizon_minutes":horizon_minutes,
                "spread_price_assumed":spread_price,"eligible_decision_times":eligible,
                "results":summary,"limitations":"Closed-price proxy only; not an execution backtest. Overlapping signals; no fees/slippage or intrabar path."}
    finally:
        con.close()


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--db", default="/data/ramon_history.sqlite3")
    p.add_argument("--symbol",default="XAUUSD_l")
    p.add_argument("--horizon-minutes", type=int,default=5)
    p.add_argument("--spread-price",type=float,default=0.42)
    a=p.parse_args()
    print(json.dumps(evaluate(a.db,a.symbol,a.horizon_minutes,a.spread_price),indent=2))

if __name__=="__main__":
    main()
