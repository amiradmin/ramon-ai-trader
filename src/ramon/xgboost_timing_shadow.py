"""Offline XGBoost timing experiment using strictly past M1 candles.

Trains a model to label whether a hypothetical direction-aligned entry
hits TP before SL in the following M1 window. No MT5 execution integration.
"""
from __future__ import annotations
import argparse
import json
import sqlite3
from pathlib import Path

def load(db, symbol, limit, timeframe="M1"):
    with sqlite3.connect(Path(db).resolve().as_uri()+"?mode=ro", uri=True) as con:
        return list(reversed(con.execute(
            "SELECT time,open,high,low,close FROM history_bars "
            "WHERE symbol=? AND timeframe=? ORDER BY time DESC LIMIT ?",
            (symbol,timeframe,limit)).fetchall()))

def build_examples(bars, *, horizon=15, stride=5, stop=2.0, target=2.0, spread=0.42, bar_seconds=60):
    """Resolve same-bar TP/SL collision as loss; no favorable lookahead."""
    import numpy as np
    data=[]
    for i in range(30,len(bars)-horizon,stride):
        segment=bars[i-20:i+horizon]
        if any(b[0]-a[0]!=bar_seconds for a,b in zip(segment,segment[1:])):
            continue
        past=bars[i-20:i]
        closes=np.array([r[4] for r in past],dtype=float)
        changes=np.diff(closes)
        volatility=float(np.std(changes))
        if volatility<=0:continue
        price=float(past[-1][4])
        for side in (1,-1):
            # Fill at adverse half spread; conservative both-target collision.
            fill=price+side*spread/2
            first=None
            for candle in bars[i:i+horizon]:
                high=float(candle[2]); low=float(candle[3])
                tp=fill+side*target
                sl=fill-side*stop
                hit_tp=(high>=tp if side==1 else low<=tp)
                hit_sl=(low<=sl if side==1 else high>=sl)
                if hit_tp or hit_sl:
                    first=int(hit_tp and not hit_sl)
                    break
            if first is None:
                continue  # Censored at horizon: do not invent a win/loss.
            features=[
                side, (closes[-1]-closes[-2])/volatility,
                (closes[-1]-closes[-5])/volatility,
                (closes[-1]-closes[-15])/volatility,
                volatility,
                (max(r[2] for r in past)-min(r[3] for r in past)),
                (closes[-1]-closes[0])/max(volatility,1e-6),
            ]
            data.append((int(bars[i][0]),features,first))
    return data

def experiment(examples, *, workers=2):
    import numpy as np
    from sklearn.metrics import roc_auc_score, brier_score_loss
    import xgboost as xgb
    examples=sorted(examples,key=lambda e:e[0])
    n=len(examples)
    if n<250:return {"status":"insufficient_samples","samples":n}
    # Chronological split; purge around time boundary by keeping gap.
    ntrain=int(n*.6); nvalid=int(n*.8)
    train=examples[:max(0,ntrain-64)]
    valid=examples[ntrain+64:max(ntrain+64,nvalid-64)]
    test=examples[nvalid+64:]
    if min(map(len,(train,valid,test)))<30:
        return {"status":"insufficient_purged_splits","samples":n}
    X=np.asarray([e[1] for e in examples],dtype=np.float32)
    y=np.asarray([e[2] for e in examples],dtype=np.int32)
    model=xgb.XGBClassifier(
        n_estimators=120,max_depth=3,learning_rate=.05,
        subsample=.8,colsample_bytree=.9,n_jobs=workers,
        objective="binary:logistic",random_state=42,eval_metric="logloss",
    )
    i1=len(train)
    i2=ntrain+64
    i3=max(ntrain+64,nvalid-64)
    i4=nvalid+64
    if len(set(y[:i1]))<2:return {"status":"single_class_train","samples":n}
    model.fit(X[:i1],y[:i1])
    def measure(xx,yy):
        scores=model.predict_proba(xx)[:,1]
        return {"samples":len(yy),"base_win_rate":float(np.mean(yy)),
                "brier":float(brier_score_loss(yy,scores)),
                "auc":float(roc_auc_score(yy,scores)) if len(set(yy))>1 else None}
    return {"status":"research_only","train":measure(X[:i1],y[:i1]),
            "validation":measure(X[i2:i3],y[i2:i3]),
            "holdout":measure(X[i4:],y[i4:]),
            "features":"M1 past OHLC derived, no M15 live inference integration",
            "caveat":"Fixed hypothetical TP/SL; no partial exit, latency or live slippage reconstruction."}

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db",required=True)
    p.add_argument("--symbol",default="XAUUSD_l")
    p.add_argument("--bars",type=int,default=100000)
    p.add_argument("--timeframe",choices=("M1","M5"),default="M1")
    p.add_argument("--horizon-minutes",type=int,default=15)
    p.add_argument("--workers",type=int,default=2)
    p.add_argument("--output",default="data/xgboost_timing_shadow.json")
    args=p.parse_args()
    if args.workers<1:p.error("workers must be positive")
    if args.horizon_minutes<5 or args.horizon_minutes%int(args.timeframe[1:]):
        p.error("--horizon-minutes must be >=5 and divisible by timeframe minutes")
    minutes=int(args.timeframe[1:])
    bars=load(args.db,args.symbol,args.bars,timeframe=args.timeframe)
    examples=build_examples(bars,horizon=args.horizon_minutes//minutes,
                            bar_seconds=minutes*60)
    result=experiment(examples,workers=args.workers)
    result.update(mode="OFFLINE_SHADOW_ONLY",symbol=args.symbol,
                  timeframe=args.timeframe,horizon_minutes=args.horizon_minutes,
                  rows=len(bars),examples=len(examples))
    target=Path(args.output);target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(json.dumps(result,indent=2)+"\\n")
    print(json.dumps(result,indent=2))

if __name__=="__main__":
    main()
