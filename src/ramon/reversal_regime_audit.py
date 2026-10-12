"""Read-only reversal analysis by UTC hour and *proxy* market regime.

Regimes below are derived exclusively from completed M1 closes, NOT Ramon's
production market_state. Never promotes a signal or enables order execution.
"""
from __future__ import annotations
import argparse
from collections import defaultdict
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sqlite3
from .entry_timing_v2_lab import _features, _signal


def _regime(closes, at):
    # Every sample comes from completed M1 bars before the decision time.
    points = [closes.get(at-i*60) for i in range(1, 17)]
    if any(v is None for v in points):
        return "UNKNOWN"
    moves = [abs(points[i]-points[i+1]) for i in range(15)]
    path = sum(moves)
    if path <= 0:
        return "FLAT"
    efficiency = abs(points[0]-points[-1])/path
    return "TREND_PROXY" if efficiency >= 0.55 else "RANGE_PROXY" if efficiency <= 0.25 else "TRANSITION_PROXY"


def _stats(rows):
    n = len(rows)
    return {"trades":n, "win_rate":sum(r[1]>0 for r in rows)/n if n else None,
            "mean_net_price_move":sum(r[1] for r in rows)/n if n else None,
            "net_price_move":sum(r[1] for r in rows),
            "sufficient_sample":n>=30}


def evaluate(db, symbol="XAUUSD_l", horizon_minutes=5, folds=4,
             train_fraction=0.5, point_size=0.01, spread_price=0.42,
             slippage_price=0.10, min_days=5):
    if not (1<=horizon_minutes<=60 and 2<=folds<=12 and
            0.3<=train_fraction<=0.8 and point_size>0 and
            spread_price>=0 and slippage_price>=0 and min_days>=2):
        raise ValueError("invalid evaluation parameters")
    with sqlite3.connect(f"file:{Path(db).resolve()}?mode=ro",uri=True) as con:
        bars=con.execute("""SELECT timeframe,time,close,spread_points
            FROM mtf_utc_bars WHERE symbol=? AND timeframe IN ('M1','M5')
            ORDER BY time""",(symbol,)).fetchall()
    m1={int(t):float(c) for tf,t,c,_ in bars if tf=="M1" and math.isfinite(c)}
    m5={int(t):float(c) for tf,t,c,_ in bars if tf=="M5" and math.isfinite(c)}
    spreads={int(t):int(sp) for tf,t,_,sp in bars if tf=="M1" and sp is not None}
    times=sorted(t+60 for t in m1)
    days={datetime.fromtimestamp(t,timezone.utc).date().isoformat() for t in times}
    train_n=int(len(times)*train_fraction)
    test_n=(len(times)-train_n)//folds
    if train_n<100 or test_n<max(30,horizon_minutes*2):
        return {"mode":"OFFLINE_OBSERVE_ONLY","status":"INSUFFICIENT_HISTORY",
                "independent_utc_days":len(days),"folds":[],"promotion_ready":False}
    folds_out=[]
    all_rows=[]
    for fold in range(folds):
        start=train_n+fold*test_n
        end=len(times) if fold==folds-1 else start+test_n
        rows=[]
        next_allowed=-1
        for at in times[start:end]:
            if at<next_allowed or at+horizon_minutes*60>times[end-1]+60:
                continue
            if any(at+j*60 not in m1 for j in range(-16,horizon_minutes)):
                continue
            features=_features(m1,at)
            if features is None:continue
            side=_signal("reversal",features,m5,at)
            if not side:continue
            points=spreads.get(at-60,0)
            cost=(points*point_size if points>0 else spread_price)+slippage_price
            outcome=side*(m1[at+(horizon_minutes-1)*60]-m1[at-60])-cost
            hour=datetime.fromtimestamp(at,timezone.utc).strftime("%H")
            regime=_regime(m1,at)
            row=(at,outcome,hour,regime)
            rows.append(row)
            all_rows.append(row)
            next_allowed=at+horizon_minutes*60
        folds_out.append({"fold":fold+1,"trades":len(rows),"stats":_stats(rows),
                          "utc_days":len({datetime.fromtimestamp(r[0],timezone.utc).date() for r in rows})})
    by_hour=defaultdict(list)
    by_regime=defaultdict(list)
    by_regime_hour=defaultdict(list)
    for r in all_rows:
        by_hour[r[2]].append(r)
        by_regime[r[3]].append(r)
        by_regime_hour[(r[3],r[2])].append(r)
    return {
        "mode":"OFFLINE_OBSERVE_ONLY",
        "status":"RESEARCH_ONLY" if len(days)>=min_days else "INSUFFICIENT_INDEPENDENT_DAYS",
        "independent_utc_days":len(days), "required_independent_days":min_days,
        "m1_rows":len(m1),"m5_rows":len(m5),
        "settings":{"folds":folds,"horizon_minutes":horizon_minutes,
            "point_size":point_size,"fallback_spread_price":spread_price,
            "slippage_price":slippage_price},
        "folds":folds_out,
        "overall":_stats(all_rows),
        "by_utc_hour":{k:_stats(v) for k,v in sorted(by_hour.items())},
        "by_proxy_regime":{k:_stats(v) for k,v in sorted(by_regime.items())},
        "by_proxy_regime_and_utc_hour":{
            f"{k[0]}/{k[1]}":_stats(v) for k,v in sorted(by_regime_hour.items())},
        "promotion_ready":False,
        "limitations":"Proxy regimes are derived from closed M1 efficiency, NOT production market_state; sample sizes below 30 are exploratory. Close-price proxy and hypothetical costs only; no tick fills, commissions, SL/TP, drawdown in account currency, or independent market-day validation."
    }


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--db",default="data/ramon_history.sqlite3")
    p.add_argument("--symbol",default="XAUUSD_l")
    p.add_argument("--horizon-minutes",type=int,default=5)
    p.add_argument("--folds",type=int,default=4)
    p.add_argument("--train-fraction",type=float,default=0.5)
    p.add_argument("--point-size",type=float,default=0.01)
    p.add_argument("--spread-price",type=float,default=0.42)
    p.add_argument("--slippage-price",type=float,default=0.10)
    p.add_argument("--min-days",type=int,default=5)
    a=p.parse_args()
    result=evaluate(a.db,a.symbol,a.horizon_minutes,a.folds,a.train_fraction,
                    a.point_size,a.spread_price,a.slippage_price,a.min_days)
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=="__main__":
    main()
