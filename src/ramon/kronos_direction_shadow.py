"""CPU-only, offline Kronos-small direction shadow benchmark.

No live integration. Requires a locally installed upstream Kronos source tree
and manually approved download of official Hugging Face checkpoints.
"""
from __future__ import annotations
import argparse
import csv
import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
import sqlite3


def fetch_bars(db, symbol, limit):
    uri = Path(db).resolve().as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as con:
        rows = con.execute(
            "SELECT time,open,high,low,close FROM history_bars "
            "WHERE symbol=? AND timeframe='M15' ORDER BY time DESC LIMIT ?",
            (symbol, limit),
        ).fetchall()
    return list(reversed(rows))


def acceptable_gap(a_time, b_time):
    """Allow normal M15 spacing plus ordinary gold-market closures."""
    a_time, b_time = int(a_time), int(b_time)
    delta = b_time - a_time
    if delta == 900:
        return True
    # Daily rollover / broker maintenance gaps.
    if 900 < delta <= 4 * 3600:
        return True
    # Weekend closure. Keep longer mid-week holes rejected as data gaps.
    a = datetime.fromtimestamp(a_time, tz=timezone.utc)
    b = datetime.fromtimestamp(b_time, tz=timezone.utc)
    if delta <= 3 * 86400 and (a.weekday() == 4 or b.weekday() in (6, 0)):
        return True
    return False


def pairs(rows, lookback, horizon, stride):
    for i in range(lookback, len(rows) - horizon + 1, stride):
        frame = rows[i-lookback:i+horizon]
        if any(not acceptable_gap(a[0], b[0]) for a,b in zip(frame, frame[1:])):
            continue
        yield i


def direction(value, baseline, min_change):
    if value > baseline + min_change:
        return "BUY"
    if value < baseline - min_change:
        return "SELL"
    return "WAIT"


def evaluate(rows, predictor, *, lookback=256, horizon=4, stride=16,
             min_change=0.0, max_samples=100):
    import pandas as pd
    output = []
    for i in pairs(rows, lookback, horizon, stride):
        history = rows[i-lookback:i]
        t0 = int(history[-1][0])
        x_df = pd.DataFrame(
            [r[1:] for r in history],
            columns=["open","high","low","close"],
        )
        x_times = pd.Series(pd.to_datetime([r[0] for r in history], unit="s", utc=True).tz_localize(None))
        future_rows = rows[i:i+horizon]
        y_times = pd.Series(pd.to_datetime([r[0] for r in future_rows], unit="s", utc=True).tz_localize(None))
        predicted = predictor.predict(
            df=x_df, x_timestamp=x_times, y_timestamp=y_times,
            pred_len=horizon, T=1.0, top_p=0.9, sample_count=1, verbose=False,
        )
        predicted_close = float(predicted["close"].iloc[-1])
        prior_close = float(history[-1][4])
        actual_close = float(future_rows[-1][4])
        forecast_side = direction(predicted_close, prior_close, min_change)
        actual_side = direction(actual_close, prior_close, min_change)
        output.append({
            "time": t0, "forecast_close": predicted_close, "actual_close": actual_close,
            "previous_close": prior_close, "forecast_direction": forecast_side,
            "actual_direction": actual_side,
            "correct": int(forecast_side == actual_side),
            "previous_bar_direction": direction(prior_close, float(history[-2][4]), min_change),
        })
        if max_samples and len(output) >= max_samples:
            break
    return output


def summarize(rows):
    if not rows:
        return {"status":"insufficient_contiguous_bars","samples":0}
    n=len(rows)
    return {
        "status":"evaluated", "samples":n,
        "direction_accuracy":sum(r["correct"] for r in rows)/n,
        "previous_bar_accuracy":sum(r["previous_bar_direction"]==r["actual_direction"] for r in rows)/n,
        "abstentions":sum(r["forecast_direction"]=="WAIT" for r in rows),
        "note":"Forecast direction accuracy only; not net trade profitability or validated improvement.",
    }


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db",required=True)
    parser.add_argument("--symbol",default="XAUUSD_l")
    parser.add_argument("--kronos-source",required=True,help="Path to cloned upstream Kronos directory")
    parser.add_argument("--lookback",type=int,default=256)
    parser.add_argument("--horizon",type=int,default=4)
    parser.add_argument("--stride",type=int,default=16)
    parser.add_argument("--max-samples",type=int,default=100)
    parser.add_argument("--bars",type=int,default=20000)
    parser.add_argument("--min-change",type=float,default=0.0)
    parser.add_argument("--output",default="data/kronos_direction_shadow.json")
    args=parser.parse_args()
    if not (2<=args.lookback<=512 and args.horizon>=1 and args.stride>=1 and args.max_samples>0):
        parser.error("invalid lookback/horizon/stride/sample count")
    sys.path.insert(0,str(Path(args.kronos_source).resolve()))
    from model import Kronos,KronosTokenizer,KronosPredictor
    tokenizer=KronosTokenizer.from_pretrained("NeoQuasar/Kronos-Tokenizer-base")
    model=Kronos.from_pretrained("NeoQuasar/Kronos-small")
    predictor=KronosPredictor(model,tokenizer,device="cpu",max_context=512)
    rows=fetch_bars(args.db,args.symbol,args.bars)
    results=evaluate(rows,predictor,lookback=args.lookback,horizon=args.horizon,
                     stride=args.stride,max_samples=args.max_samples,min_change=args.min_change)
    report={"mode":"OFFLINE_SHADOW_ONLY","model":"NeoQuasar/Kronos-small",
            "symbol":args.symbol,"horizon":args.horizon,
            "metrics":summarize(results),"predictions":results}
    target=Path(args.output)
    target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(json.dumps(report,indent=2)+"\\n")
    print(json.dumps(report["metrics"],indent=2))


if __name__=="__main__":
    main()
