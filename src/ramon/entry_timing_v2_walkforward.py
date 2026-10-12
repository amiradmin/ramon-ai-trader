"""Read-only walk-forward audit of Entry Timing v2, using closed MT5 bars.

No live order controls are imported or changed. Price-distance PnL is not
broker account money; close-to-close proxy excludes tick path/SL/TP/commissions.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sqlite3

from .entry_timing_v2_lab import STRATEGIES, _features, _signal


def _stats(trades):
    n = len(trades)
    equity = peak = drawdown = 0.0
    wins = 0
    hourly = defaultdict(lambda: {"trades": 0, "net": 0.0})
    for at, net in trades:
        wins += net > 0
        equity += net
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
        h = str(datetime.fromtimestamp(at, timezone.utc).hour).zfill(2)
        hourly[h]["trades"] += 1
        hourly[h]["net"] += net
    return {"trades": n, "win_rate": wins / n if n else None,
            "mean_net_price_move": equity / n if n else None,
            "total_net_price_move": equity,
            "max_drawdown_price": drawdown,
            "utc_hour": dict(sorted(hourly.items()))}


def evaluate(db, *, symbol="XAUUSD_l", horizon_minutes=5, folds=4,
             train_fraction=0.5, spread_price=0.42, point_size=0.01,
             slippage_price=0.10, use_observed_spread=True):
    if not (1 <= horizon_minutes <= 60 and 2 <= folds <= 12
            and 0.3 <= train_fraction <= 0.8 and spread_price >= 0
            and point_size > 0 and slippage_price >= 0):
        raise ValueError("invalid evaluation settings")
    con = sqlite3.connect(f"file:{Path(db).resolve()}?mode=ro", uri=True)
    try:
        data = con.execute("""SELECT timeframe,time,close,spread_points
            FROM mtf_utc_bars WHERE symbol=? AND timeframe IN ('M1','M5')
            ORDER BY time""", (symbol,)).fetchall()
    finally:
        con.close()
    m1 = {int(t): float(c) for tf,t,c,_ in data if tf=="M1" and math.isfinite(c)}
    m5 = {int(t): float(c) for tf,t,c,_ in data if tf=="M5" and math.isfinite(c)}
    spread = {int(t): int(sp) for tf,t,_,sp in data if tf=="M1" and sp is not None}
    times = sorted(t+60 for t in m1)
    n = len(times)
    min_train = int(n*train_fraction)
    remainder = n-min_train
    test_block = remainder//folds if folds else 0
    # Fail closed for very short data histories; evaluate more independent sessions.
    if min_train < 100 or test_block < max(30, horizon_minutes*2):
        return {"mode":"OFFLINE_OBSERVE_ONLY", "status":"INSUFFICIENT_HISTORY",
                "m1_rows":len(m1), "m5_rows":len(m5), "folds":[],
                "promotion_ready":False}
    all_tests = {name: [] for name in STRATEGIES}
    fold_rows = []
    for fold in range(folds):
        cut = min_train + fold*test_block
        end = n if fold == folds-1 else min(n,cut+test_block)
        border = times[cut]
        # Outcome from training may not overlap future test data.
        embargo = horizon_minutes*60
        sample = {}
        for strategy in STRATEGIES:
            train_trades = []
            test_trades = []
            for phase, candidates in (
                ("train", times[:cut]), ("test", times[cut:end])):
                next_allowed = -1
                for at in candidates:
                    if at < next_allowed: continue
                    if phase=="train" and at + embargo >= border: continue
                    if phase=="test" and at+horizon_minutes*60 > (times[end-1]+60): continue
                    feature = _features(m1, at)
                    if feature is None: continue
                    if any(at+j*60 not in m1 for j in range(-5,horizon_minutes)):
                        continue
                    side = _signal(strategy, feature, m5, at)
                    if not side: continue
                    sp = spread.get(at-60, 0)
                    cost = (sp*point_size if use_observed_spread and sp>0 else spread_price)
                    net = side*(m1[at+(horizon_minutes-1)*60]-m1[at-60])-cost-slippage_price
                    (train_trades if phase=="train" else test_trades).append((at,net))
                    next_allowed = at + horizon_minutes*60
            sample[strategy] = {"train": _stats(train_trades), "test":_stats(test_trades)}
        eligible = [name for name in STRATEGIES if sample[name]["train"]["trades"] >= 20]
        selected = (max(eligible,key=lambda s:sample[s]["train"]["mean_net_price_move"])
                    if eligible else None)
        if selected:
            all_tests[selected].extend(sample[selected]["test"].get("trades",[]) * [])  # counts reported below
        fold_rows.append({"fold":fold+1,"train_until_utc":border,
                          "test_from_utc":border,"test_until_utc":times[end-1],
                          "selected_from_train":selected, "strategies":sample,
                          "selected_holdout":sample[selected]["test"] if selected else None})
    selections = [x["selected_from_train"] for x in fold_rows]
    # Treat every fold as diagnostics, NOT independent statistical experiments
    # because expanding training windows overlap.
    return {"mode":"OFFLINE_OBSERVE_ONLY","status":"RESEARCH_ONLY",
            "symbol":symbol,"m1_rows":len(m1),"m5_rows":len(m5),
            "settings":{"folds":folds,"horizon_minutes":horizon_minutes,
                       "train_fraction":train_fraction,"observed_spread":use_observed_spread,
                       "fallback_spread_price":spread_price,"point_size":point_size,
                       "slippage_price":slippage_price},
            "folds":fold_rows, "train_selected_by_fold":selections,
            "promotion_ready":False,
            "limitations":"Close-based proxy; hour is UTC, not market session; expanding training overlaps; slippage hypothetical; no tick SL/TP, execution, commissions, volume, capital drawdown, or independent market days."}


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--db",default="data/ramon_history.sqlite3")
    p.add_argument("--symbol",default="XAUUSD_l")
    p.add_argument("--folds",type=int,default=4)
    p.add_argument("--horizon-minutes",type=int,default=5)
    p.add_argument("--train-fraction",type=float,default=0.5)
    p.add_argument("--spread-price",type=float,default=0.42)
    p.add_argument("--point-size",type=float,default=0.01)
    p.add_argument("--slippage-price",type=float,default=0.10)
    p.add_argument("--fixed-spread",action="store_true")
    a=p.parse_args()
    result=evaluate(a.db,symbol=a.symbol,folds=a.folds,horizon_minutes=a.horizon_minutes,
                    train_fraction=a.train_fraction,spread_price=a.spread_price,
                    point_size=a.point_size,slippage_price=a.slippage_price,
                    use_observed_spread=not a.fixed_spread)
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=="__main__":
    main()
