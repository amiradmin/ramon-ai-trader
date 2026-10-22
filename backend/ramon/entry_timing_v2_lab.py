"""Entry Timing v2 offline research. Read-only; never authorizes orders.

Closed candle only, chronological holdout, non-overlapping position proxy.
Results are PRICE differences, not account profit or tick-execution results.
"""
from __future__ import annotations

import argparse
import json
import math
import sqlite3
from pathlib import Path

STRATEGIES = ("momentum", "mean_reversion", "reversal", "mtf_agreement")


def _direction(change: float) -> int:
    return (change > 0) - (change < 0)


def _features(closes: dict[int, float], at: int) -> tuple[int, int, int] | None:
    # The latest readable M1 bar at decision time 'at' opened at at-60.
    last = [closes.get(at - (i + 1) * 60) for i in range(5)]
    if any(v is None for v in last):
        return None
    # Price signal may use data only from completed bars.
    fast = _direction(last[0] - last[3])
    slow = _direction(last[0] - last[4])
    turn = _direction(last[0] - last[1])
    return fast, slow, turn


def _signal(strategy: str, features: tuple[int, int, int], m5: dict[int, float], at: int) -> int:
    fast, slow, turn = features
    if strategy == "momentum":
        return fast
    if strategy == "mean_reversion":
        return -fast
    if strategy == "reversal":
        return turn if turn and fast and turn != fast else 0
    if strategy == "mtf_agreement":
        # M5 opening timestamps are aligned to 300 s; latest *closed* is
        # the bar preceding the current five-minute interval.
        bar = at // 300 * 300 - 300
        cur, prev = m5.get(bar), m5.get(bar - 900)
        if cur is None or prev is None:
            return 0
        m5_side = _direction(cur - prev)
        return fast if fast and fast == m5_side else 0
    raise ValueError(strategy)


def _stats(trades: list[float]) -> dict:
    n = len(trades)
    return {"trades": n, "win_rate": sum(v > 0 for v in trades) / n if n else None,
            "mean_net_price_move": sum(trades) / n if n else None,
            "total_net_price_move": sum(trades) if n else 0.0}


def evaluate(db: str | Path, symbol: str = "XAUUSD_l", horizon_minutes: int = 5,
             spread_price: float = 0.42, slippage_price: float = 0.0,
             train_fraction: float = 0.7) -> dict:
    if not (1 <= horizon_minutes <= 60 and 0 <= spread_price < 100
            and 0 <= slippage_price < 100 and 0.5 <= train_fraction <= 0.9):
        raise ValueError("invalid evaluation parameters")
    con = sqlite3.connect(f"file:{Path(db).resolve()}?mode=ro", uri=True)
    try:
        data = con.execute("""SELECT timeframe,time,close FROM mtf_utc_bars
            WHERE symbol=? AND timeframe IN ('M1','M5') ORDER BY time""",(symbol,)).fetchall()
    finally:
        con.close()
    m1 = {int(t):float(close) for tf,t,close in data if tf == "M1" and math.isfinite(close)}
    m5 = {int(t):float(close) for tf,t,close in data if tf == "M5" and math.isfinite(close)}
    times = sorted(t+60 for t in m1)
    # Strict chronological split by time, leaving horizon-size embargo
    # before test begins. No feature fitting on the test period.
    split = int(len(times)*train_fraction)
    boundary = times[split] if split < len(times) else None
    embargo_seconds = horizon_minutes * 60
    results = {}
    for strategy in STRATEGIES:
        per_split = {"train": [], "test": []}
        ignored = {"no_signal":0,"missing_bars":0,"embargo":0}
        next_eligible = {"train":-1,"test":-1}
        for at in times:
            phase = "train" if boundary is None or at < boundary else "test"
            if phase == "train" and boundary is not None and at + embargo_seconds >= boundary:
                ignored["embargo"] += 1
                continue
            if at < next_eligible[phase]:
                continue
            features = _features(m1, at)
            entry = m1.get(at-60)
            exit_ = m1.get(at+(horizon_minutes-1)*60)
            if features is None or entry is None or exit_ is None:
                ignored["missing_bars"] += 1
                continue
            # Reject discontinuous M1 windows, including future outcome gap.
            if any(at + j*60 not in m1 for j in range(-5, horizon_minutes)):
                ignored["missing_bars"] += 1
                continue
            side = _signal(strategy, features, m5, at)
            if side == 0:
                ignored["no_signal"] += 1
                continue
            net = side*(exit_-entry)-spread_price-slippage_price
            per_split[phase].append(net)
            next_eligible[phase] = at+horizon_minutes*60
        results[strategy] = {"train":_stats(per_split["train"]),
                             "test":_stats(per_split["test"]), "skipped":ignored}
    # Select solely on training. Test is reported but never used for selection.
    viable = [name for name in STRATEGIES if results[name]["train"]["trades"] >= 20]
    champion = max(viable, key=lambda name: results[name]["train"]["mean_net_price_move"]) if viable else None
    champion_test = results[champion]["test"] if champion else None
    return {"mode":"OFFLINE_OBSERVE_ONLY","symbol":symbol,
            "horizon_minutes":horizon_minutes, "spread_price":spread_price,
            "slippage_price":slippage_price, "train_fraction":train_fraction,
            "embargo_seconds":embargo_seconds, "m1_rows":len(m1),"m5_rows":len(m5),
            "strategies":results, "train_selected":champion,
            "selected_holdout":champion_test,
            "promotion_ready":False,
            "limitations":"Closed prices proxy; no tick fill, intrabar stops, swap, commission, real execution or multiple independent folds. Dataset may cover only a short session."}


def main() -> None:
    p = argparse.ArgumentParser(description="Read-only Entry Timing v2 research")
    p.add_argument("--db",default="data/ramon_history.sqlite3")
    p.add_argument("--symbol",default="XAUUSD_l")
    p.add_argument("--horizon-minutes",type=int,default=5)
    p.add_argument("--spread-price",type=float,default=0.42)
    p.add_argument("--slippage-price",type=float,default=0.0)
    p.add_argument("--train-fraction",type=float,default=0.7)
    a=p.parse_args()
    print(json.dumps(evaluate(a.db,a.symbol,a.horizon_minutes,a.spread_price,
                              a.slippage_price,a.train_fraction),indent=2))


if __name__=="__main__":
    main()
