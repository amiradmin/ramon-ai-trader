"""Conservative, read-only Early Exit replay validator.

OHLC bars are diagnostic evidence, not proof of executable broker fills.
No counterfactual P&L is emitted without verified SL/TP, bid/ask and fees.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
import math
from pathlib import Path
import sqlite3

from .evaluation_lab import evaluate


def _num(value):
    try:
        x = float(value)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def _schema(db, table):
    return {r[1] for r in db.execute("PRAGMA table_info(" + table + ")")}


def _bars(db, symbol, timeframe, start, end, max_rows=2000):
    rows = db.execute(
        "SELECT time,open,high,low,close,spread_points FROM history_bars "
        "WHERE symbol=? AND timeframe=? AND time>=? AND time<? "
        "ORDER BY time ASC LIMIT ?", (symbol, timeframe, start, end, max_rows + 1)
    ).fetchall()
    return [dict(r) for r in rows[:max_rows]], len(rows)>max_rows


def validate(db_path, symbol="XAUUSD_l", lookahead_minutes=60, limit=50000):
    base = evaluate(db_path, symbol, limit)
    if not base.get("available"):
        return base
    path = Path(db_path).expanduser().resolve()
    lookahead_minutes = max(1, min(240, int(lookahead_minutes)))
    candidate_ids = {r["sample_key"] for r in base["early_exit_replay"]["per_trade"]}
    results, reasons = [], Counter()
    try:
        with sqlite3.connect(path.as_uri()+"?mode=ro", uri=True, timeout=2) as db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA query_only=ON")
            db.execute("BEGIN")
            dcols, tcols, bcols = (_schema(db,t) for t in ("decision_samples","trade_outcomes","history_bars"))
            if not {"symbol","timeframe","time","open","high","low","close","spread_points"}.issubset(bcols):
                return {"available":False,"reason":"HISTORY_BARS_SCHEMA_MISSING"}
            required={"symbol","sample_key","opened","closed","direction","actual_fill_price","exit_detail"}
            if not required.issubset(tcols):
                return {"available":False,"reason":"TRADE_SCHEMA_MISSING","missing":sorted(required-tcols)}
            if not {"symbol","sample_key","stop_distance","target_distance"}.issubset(dcols):
                return {"available":False,"reason":"DECISION_SCHEMA_MISSING"}
            for key in sorted(candidate_ids):
                trades=db.execute(
                    "SELECT * FROM trade_outcomes WHERE symbol=? AND sample_key=? AND exit_detail='early_adverse_exit'",
                    (symbol,key)).fetchall()
                if len(trades)!=1:
                    results.append({"sample_key":key,"status":"AMBIGUOUS_TRADE_IDENTITY","matches":len(trades)})
                    reasons["AMBIGUOUS_TRADE_IDENTITY"]+=1
                    continue
                t=dict(trades[0]); entries=db.execute(
                    "SELECT stop_distance,target_distance FROM decision_samples WHERE symbol=? AND sample_key=?",
                    (symbol,key)).fetchall()
                blockers=[]
                if len(entries)!=1:
                    blockers.append("DECISION_JOIN_NOT_UNIQUE")
                opened, closed = _num(t["opened"]),_num(t["closed"])
                fill, stop, target = _num(t["actual_fill_price"]), None, None
                if entries and len(entries)==1:
                    stop, target = _num(entries[0]["stop_distance"]),_num(entries[0]["target_distance"])
                if fill is None or fill<=0: blockers.append("ENTRY_FILL_MISSING")
                if stop is None or stop<=0 or target is None or target<=0: blockers.append("PLAN_SL_TP_MISSING")
                if opened is None or closed is None or opened<=0 or closed<=opened: blockers.append("INVALID_TRADE_TIMES")
                bars={}
                if closed is not None and closed>0:
                    for tf, seconds in (("M1",60),("M5",300)):
                        start=int(closed)//seconds*seconds
                        end=start+lookahead_minutes*60+seconds
                        selected,truncated=_bars(db,symbol,tf,start,end)
                        times=[int(x["time"]) for x in selected]
                        distinct=len(times)==len(set(times))
                        continuity=bool(times) and times[0]==start and all(
                            b-a==seconds for a,b in zip(times,times[1:]))
                        required_count=(lookahead_minutes*60)//seconds + 1
                        coverage=len(times)>=required_count and continuity and distinct and not truncated
                        bars[tf]={"rows":len(times),"required_rows":required_count,
                                  "first_bar":times[0] if times else None,
                                  "last_bar":times[-1] if times else None,
                                  "continuous":continuity and distinct,
                                  "coverage_complete":coverage,"truncated":truncated}
                    if not bars["M1"]["coverage_complete"]: blockers.append("M1_COVERAGE_INCOMPLETE")
                else:
                    blockers.append("BAR_WINDOW_UNAVAILABLE")
                blockers += ["BROKER_INITIAL_SL_TP_UNVERIFIED","EXECUTABLE_BID_ASK_TICKS_UNVERIFIED",
                             "ORDER_FILL_AND_FEES_UNVERIFIED"]
                side=str(t["direction"]).upper()
                planned = None
                if fill and stop and target and side in ("BUY","SELL"):
                    planned={"sl":round(fill-stop if side=="BUY" else fill+stop,5),
                             "tp":round(fill+target if side=="BUY" else fill-target,5),
                             "source":"MODEL_PLANNED_NOT_BROKER_VERIFIED"}
                item={"sample_key":key,"trade_key":t.get("trade_key"),"direction":side,
                      "opened":opened,"closed":closed,"actual_fill_price":fill,
                      "planned_levels":planned,"bars":bars,
                      "status":"INSUFFICIENT_EXECUTION_EVIDENCE",
                      "blockers":sorted(set(blockers)), "hypothetical_net_units":None,
                      "outcome":"NOT_EVALUATED"}
                results.append(item)
                for name in item["blockers"]: reasons[name]+=1
    except (sqlite3.Error,OSError,ValueError) as exc:
        return {"available":False,"reason":"READ_FAILED","error_type":type(exc).__name__}
    return {"available":True,"read_only":True,"symbol":symbol,
            "lookahead_minutes":lookahead_minutes,"candidate_trades":len(candidate_ids),
            "validated_rows":len(results),"blocker_counts":dict(reasons),
            "trades":results,"counterfactual_status":"NOT_EVALUATED",
            "policy":"No OHLC-only PnL or assumed executable bid/ask; never changes EA."}


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--db",default="data/ramon_history.sqlite3")
    p.add_argument("--symbol",default="XAUUSD_l")
    p.add_argument("--lookahead-minutes",type=int,default=60)
    args=p.parse_args()
    print(json.dumps(validate(args.db,args.symbol,args.lookahead_minutes),ensure_ascii=False,indent=2))


if __name__=="__main__":
    main()
