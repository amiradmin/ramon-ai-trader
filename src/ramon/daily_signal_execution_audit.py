"""Read-only signal-to-trade and WAIT-follow-through audit; no counterfactual P/L."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3


def _json(value):
    try:
        d = json.loads(value or "{}")
        return d if isinstance(d, dict) else {}
    except (TypeError, ValueError):
        return {}


def audit(db: str | Path, day: str, symbol: str = "XAUUSD_l", horizon: int = 4):
    if not 1 <= horizon <= 24:
        raise ValueError("horizon must be 1..24 M15 bars")
    start = int(datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())
    path = Path(db).expanduser().resolve()
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as con:
        con.row_factory = sqlite3.Row
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "decision_samples" not in tables:
            raise ValueError("missing decision_samples")
        cols = {r[1] for r in con.execute("PRAGMA table_info(decision_samples)")}
        mandatory = {"captured","symbol","signal_bar_time","direction","final_decision","model_metadata","sample_key"}
        if not mandatory <= cols:
            raise ValueError("missing decision columns: "+repr(sorted(mandatory-cols)))
        decisions = [dict(r) for r in con.execute(
            "SELECT captured,signal_bar_time,direction,final_decision,model_metadata,sample_key "
            "FROM decision_samples WHERE symbol=? AND captured>=? AND captured<? ORDER BY captured,id",
            (symbol,start,start+86400))]
        trades = []
        if "trade_outcomes" in tables:
            tc = {r[1] for r in con.execute("PRAGMA table_info(trade_outcomes)")}
            extras = ",".join(x if x in tc else "NULL AS "+x for x in
                              ("entry_source","entry_magic","entry_ea_version","actual_fill_price"))
            trades = [dict(r) for r in con.execute(
                "SELECT sample_key,trade_key,direction,opened,closed,"+extras+
                " FROM trade_outcomes WHERE symbol=? AND opened>=? AND opened<?",
                (symbol,start,start+86400))]
        bars = {}
        if "history_bars" in tables:
            bars = {r["time"]: r["close"] for r in con.execute(
                "SELECT time,close FROM history_bars WHERE symbol=? AND timeframe='M15' "
                "AND time>=? AND time<?", (symbol,start-86400,start+86400+24*900))}
    by_key = defaultdict(list)
    for trade in trades:
        if trade["sample_key"]:
            by_key[trade["sample_key"]].append(trade)
    signals = []
    groups = defaultdict(list)
    classifications = Counter()
    for d in decisions:
        audit_info = _json(_json(d["model_metadata"]).get("decision_audit"))
        final = _json(audit_info.get("final"))
        decision = str(final.get("decision") or d["final_decision"] or "UNKNOWN").upper()
        reason = str(final.get("reason") or "UNKNOWN")
        direction = str(d["direction"] or "").upper()
        side = direction if direction in {"BUY","SELL"} else "UNKNOWN"
        groups[(d["signal_bar_time"],side)].append((decision,reason,d))
        if decision not in {"BUY","SELL"}:
            continue
        matched = by_key.get(d["sample_key"], []) if d["sample_key"] else []
        same_side = [t for t in matched if str(t["direction"]).upper()==decision]
        if same_side:
            status = "RECORDED_TRADE_SAME_SAMPLE_AND_SIDE_NOT_ORDER_PROOF"
        elif matched:
            status = "RECORDED_TRADE_SAME_SAMPLE_DIFFERENT_SIDE"
        else:
            status = "NO_SAME_SAMPLE_RECORDED_TRADE"
        classifications[status] += 1
        signals.append({"captured_utc":d["captured"],"sample_key":d["sample_key"],
            "signal_bar_time":d["signal_bar_time"],"decision":decision,
            "reason":reason,"match_status":status,
            "trade_keys":[t["trade_key"] for t in matched],
            "recorded_entry_sources":[t["entry_source"] for t in matched],
            "caution":"A sample-key match is not broker order-attempt or EA authorization evidence."})
    wait_groups = []
    missing_history = Counter()
    for (bar,side), entries in sorted(groups.items()):
        wait = [(decision,reason,d) for decision,reason,d in entries if decision=="WAIT"]
        if not wait:
            continue
        reason_set = sorted({reason for _,reason,_ in wait})
        if side not in {"BUY","SELL"}:
            movement = {"status":"UNKNOWN_CANDIDATE_DIRECTION"}
        elif bar not in bars or any(bar+900*i not in bars for i in range(1,horizon+1)):
            movement = {"status":"MISSING_CONTIGUOUS_M15_BARS"}
        else:
            # This is a raw close-to-close move, not executable opportunity P/L.
            delta = bars[bar+900*horizon]-bars[bar]
            signed = delta if side=="BUY" else -delta
            movement = {"status":"OBSERVED_COMPLETE_M15","favorable_direction_close_move":round(signed,5),
                        "horizon_bars":horizon,
                        "warning":"No intrabar path, spread, fees, fill, SL or TP assumptions; NOT profit."}
        missing_history[movement["status"]] += 1
        wait_groups.append({"signal_bar_time":bar,"candidate_side":side,
            "wait_sample_count":len(wait),"wait_reasons":reason_set,
            "also_had_final_trade_signal":any(x[0] in {"BUY","SELL"} for x in entries),
            "future_close_observation":movement})
    return {"day_utc":day,"symbol":symbol,"mode":"OFFLINE_READ_ONLY",
        "saved_buy_sell_decisions":len(signals),
        "saved_signal_trade_link_status":dict(classifications),
        "signals":signals,"wait_opportunity_groups":len(wait_groups),
        "wait_future_observation_coverage":dict(missing_history),
        "wait_groups":wait_groups,
        "limitations":[
            "trade_outcomes contains completed recorded trades, not an exhaustive account of open positions or rejected orders.",
            "A saved final BUY/SELL is not proof that MT5 received or attempted an order.",
            "Same sample_key joins do not distinguish human-assisted overrides from autonomous authorization.",
            "Candidate direction reflects saved direction field, which may differ from later intrabar proposal.",
            "Future M15 close change is descriptive and cannot establish missed profitability.",
            "Signal bar times are interpreted as UTC; validate broker timestamp normalization before using follow-through values.",
            "No controls, live orders, datasets or stored rows are modified."]}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db",default="data/ramon_history.sqlite3")
    p.add_argument("--day",required=True)
    p.add_argument("--symbol",default="XAUUSD_l")
    p.add_argument("--horizon-bars",type=int,default=4)
    p.add_argument("--output")
    args=p.parse_args()
    result=audit(args.db,args.day,args.symbol,args.horizon_bars)
    payload=json.dumps(result,ensure_ascii=False,indent=2)
    if args.output:
        Path(args.output).write_text(payload+"\n",encoding="utf-8")
    print(payload)


if __name__=="__main__":
    main()
