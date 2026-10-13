"""Read-only, time-bounded review of Ramon's observed decisions and trade outcomes."""
from __future__ import annotations
import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import time


def _json(value):
    try:
        result=json.loads(value or "{}")
        return result if isinstance(result,dict) else {}
    except (TypeError,ValueError):
        return {}


def _safe(v):
    if v is None or isinstance(v,(str,int,float,bool)):
        return v
    return str(v)


def audit(db_path, symbol="XAUUSD_l", since_utc=None, until_utc=None, limit=20000):
    now=int(time.time())
    until=int(until_utc) if until_utc is not None else now
    since=int(since_utc) if since_utc is not None else until-8*3600
    if not (0<since<until<=now+300 and until-since<=7*86400):
        return {"available":False,"reason":"INVALID_TIME_WINDOW"}
    limit=max(1,min(100000,int(limit)))
    path=Path(db_path).expanduser().resolve()
    if not path.is_file():
        return {"available":False,"reason":"HISTORY_DB_MISSING"}
    try:
        with sqlite3.connect(path.as_uri()+"?mode=ro",uri=True,timeout=2) as db:
            db.row_factory=sqlite3.Row
            db.execute("PRAGMA query_only=ON")
            columns={x[1] for x in db.execute("PRAGMA table_info(decision_samples)")}
            required={"symbol","captured","final_decision","model_metadata"}
            if not required.issubset(columns):
                return {"available":False,"reason":"DECISION_SCHEMA_MISSING","missing":sorted(required-columns)}
            total=db.execute("SELECT COUNT(*) FROM decision_samples WHERE symbol=? AND captured>=? AND captured<?",
                             (symbol,since,until)).fetchone()[0]
            order="id" if "id" in columns else "rowid"
            rows=db.execute(f"SELECT * FROM decision_samples WHERE symbol=? AND captured>=? AND captured<? ORDER BY {order} DESC LIMIT ?",
                            (symbol,since,until,limit)).fetchall()
            tcols={x[1] for x in db.execute("PRAGMA table_info(trade_outcomes)")}
            trades=[]
            if {"symbol","opened"}.issubset(tcols):
                trades=[dict(x) for x in db.execute(
                    "SELECT * FROM trade_outcomes WHERE symbol=? AND opened>=? AND opened<? ORDER BY opened DESC LIMIT 5000",
                    (symbol,since,until))]
    except (sqlite3.Error,OSError) as exc:
        return {"available":False,"reason":"READ_FAILED","error_type":type(exc).__name__}
    reasons=Counter(); states=Counter(); decisions=Counter(); conflicts=0; cases=[]
    for record in rows:
        row=dict(record);meta=_json(row.get("model_metadata"))
        decision_audit=meta.get("decision_audit")
        if not isinstance(decision_audit,dict):decision_audit={}
        final=decision_audit.get("final")
        if not isinstance(final,dict):final={}
        base=decision_audit.get("base")
        if not isinstance(base,dict):base={}
        market=meta.get("market_assessment")
        if not isinstance(market,dict):market={}
        decision=str(row.get("final_decision") or "UNKNOWN").upper()
        reason=str(row.get("final_reason") or final.get("reason") or row.get("reason") or "UNKNOWN")[:120]
        state=str(final.get("market_state") or market.get("state") or "UNKNOWN")[:80]
        candidate=str(final.get("candidate_direction") or "")
        market_dir=str(final.get("market_direction") or "")
        conflict=candidate in {"BUY","SELL"} and market_dir in {"BUY","SELL"} and candidate!=market_dir
        decisions[decision]+=1; reasons[reason]+=1; states[state]+=1; conflicts+=int(conflict)
        cases.append({
            "captured":row.get("captured"),"sample_key":row.get("sample_key"),
            "decision":decision,"reason":reason,"market_state":state,
            "candidate_direction":candidate,"market_direction":market_dir,
            "direction_conflict":conflict,"base_reason":_safe(base.get("reason")),
            "strength":_safe(base.get("signal_strength")),
            "minimum_strength":_safe(base.get("minimum_strength")),
            "metadata_present":bool(meta),
        })
    # Decisions and execution outcomes are distinct streams. Never infer filled
    # trades from BUY/SELL decisions or the absence of a trade_outcomes row.
    trade_source_counts=Counter(str(t.get("entry_source") or "UNKNOWN") for t in trades)
    return {
        "available":True,"read_only":True,"symbol":symbol,"since_utc":since,
        "until_utc":until,"total_decision_samples":total,
        "sampled_decision_samples":len(rows),"truncated":total>len(rows),
        "decisions":dict(decisions),"reasons":dict(reasons.most_common(40)),
        "market_states":dict(states.most_common(25)),"direction_conflicts":conflicts,
        "recorded_trade_outcomes_opened":len(trades),
        "trade_outcome_sources":dict(trade_source_counts),
        "cases":cases[:150],
        "assessment":"OBSERVED_GATE_AUDIT_ONLY",
        "missed_profit_or_should_have_entered":"NOT_EVALUATED",
        "limitations":["Historical decision samples do not prove each MT5 order gate, latency or terminal authorization.",
                       "Trade outcomes may be delayed or absent; execution requires independent EA/broker evidence.",
                       "No hindsight assertion of a profitable missed trade."],
    }


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--db",default="data/ramon_history.sqlite3")
    p.add_argument("--since-utc",type=int,required=True)
    p.add_argument("--until-utc",type=int)
    p.add_argument("--symbol",default="XAUUSD_l")
    args=p.parse_args()
    print(json.dumps(audit(args.db,args.symbol,args.since_utc,args.until_utc),ensure_ascii=False,indent=2,allow_nan=False))


if __name__=="__main__":
    main()
