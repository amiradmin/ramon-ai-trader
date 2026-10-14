"""Read-only, bounded opportunity and realized-loss review for Ramon.

A WAIT is never counted as a profitable missed trade without a validated fill,
execution permissions, costs, and future path. This report is diagnostic only.
"""
import os
import time
from .shadow_missed_replay import replay_report
from .prevention_plan import prevention_plan
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

def _group_trades(rows):
    result={}
    for r in rows:
        reason=str(r.get("exit_reason") or "UNKNOWN")
        b=result.setdefault(reason,{"reason":reason,"count":0,"net_units":0.0})
        b["count"]+=1
        b["net_units"]+=float(r["net_units"])
    return sorted((dict(x,net_units=round(x["net_units"],3)) for x in result.values()),
                  key=lambda x:x["net_units"])

def review(symbol="XAUUSD_l", since=None, until=None):
    now=int(time.time())
    until=now if until is None else int(until)
    since=until-86400 if since is None else int(since)
    if not (0<since<until<=now+300 and until-since<=7*86400):
        return {"available":False,"reason":"INVALID_TIME_WINDOW"}
    if os.getenv("RAMON_STORAGE_BACKEND","").lower()!="postgres":
        return {"available":False,"reason":"POSTGRES_REQUIRED"}
    try:
        import psycopg
        from psycopg.rows import dict_row
        with psycopg.connect(os.environ["RAMON_PG_DSN"],connect_timeout=3,
                options="-c default_transaction_read_only=on -c statement_timeout=7000",
                row_factory=dict_row) as db:
            with db.cursor() as c:
                c.execute("""SELECT count(*) AS n FROM ramon.decision_samples
                             WHERE symbol=%s AND captured>=%s AND captured<%s""",(symbol,since,until))
                total=int(c.fetchone()["n"])
                c.execute("""SELECT sample_key,captured,signal_bar_time,final_decision,spread,
                        metadata->'model_metadata' AS model_metadata
                        FROM ramon.decision_samples
                        WHERE symbol=%s AND captured>=%s AND captured<%s
                        ORDER BY captured DESC LIMIT 20000""",(symbol,since,until))
                decisions=c.fetchall()
                c.execute("""SELECT trade_key,sample_key,opened,closed,received,direction,
                             exit_reason,net_units,initial_risk_units,net_r,telemetry
                             FROM ramon.trade_outcomes WHERE symbol=%s AND
                             received>=%s AND received<%s
                             ORDER BY received DESC LIMIT 5000""",(symbol,since,until))
                trades=c.fetchall()
                c.execute("""SELECT time,open,high,low,close FROM ramon.mtf_utc_bars
                    WHERE symbol=%s AND timeframe='M1'
                    AND time>=%s AND time<=%s ORDER BY time LIMIT 15000""",
                    (symbol,since,until+3600))
                m1=[dict(r) for r in c.fetchall()]
        trade_cases=[]
        for t in trades:
            metadata=t.get("telemetry") or {}
            if str(metadata.get("trade_role") or "MAIN").upper() not in ("MAIN","PRIMARY"):
                continue
            trade_cases.append({"trade_key":t["trade_key"],"sample_key":t["sample_key"],
                 "closed":t["closed"],"net_units":t["net_units"],"net_r":t["net_r"],
                 "initial_risk_units":t["initial_risk_units"],"direction":t["direction"],
                 "exit_reason":t["exit_reason"],
                 "exit_detail":metadata.get("exit_detail"),
                 "entry_source":metadata.get("entry_source") or "UNKNOWN",
                 "entry_ea_version":metadata.get("entry_ea_version") or "UNKNOWN"})
        trade_cases.sort(key=lambda t:float(t["net_units"]))
        loss_cases=[t for t in trade_cases if float(t["net_units"])<0]
        sample_keys={t["sample_key"] for t in trades}
        from collections import Counter
        reasons=Counter(); wait=0; recent_wait=[]
        for d in decisions:
            if str(d["final_decision"]).upper()!="WAIT":
                continue
            wait+=1
            meta=d.get("model_metadata") or {}
            if not isinstance(meta,dict):meta={}
            audit=meta.get("decision_audit") or {}
            final=audit.get("final") or {}
            reason=str(final.get("reason") or "UNKNOWN")
            reasons[reason]+=1
            if len(recent_wait)<60:
                recent_wait.append({"sample_key":d["sample_key"],"captured":d["captured"],
                   "reason":reason,"candidate_direction":final.get("candidate_direction"),
                   "executed_outcome_in_window":d["sample_key"] in sample_keys,
                   "potential_profit":"NOT_EVALUATED",
                   "required_evidence":"M1/tick replay, spread, broker order gates, risk, SL/TP"})
        shadow = replay_report(decisions,m1)
        return {"available":True,"read_only":True,"shadow_replay":shadow,
                "prevention_plan":prevention_plan(shadow, loss_cases),"symbol":symbol,"since_utc":since,
                "until_utc":until,"decision_samples":len(decisions),"decision_samples_total":total,
                "decision_samples_truncated":total>len(decisions),
                "wait_decisions":wait,"wait_reasons":dict(reasons.most_common(20)),
                "recorded_closed_outcomes_received":len(trade_cases),
                "losing_outcomes":len(loss_cases),
                "realized_net_units":round(sum(float(t["net_units"]) for t in trade_cases),3),
                "loss_by_exit_reason":_group_trades(loss_cases),
                "worst_losses":loss_cases[:30],"wait_cases":recent_wait,
                "profitable_missed_positions":{"status":"NOT_ESTABLISHED",
                    "count":None,"explanation":"WAIT snapshots are not independent filled opportunities; cannot claim 10 missed winners without broker-aware out-of-sample replay."},
                "improvement_actions":[
                    {"action":"Audit large SL/SO losses against initial risk, min-lot override, and MT5 deals",
                     "status":"RESEARCH_ONLY","live_change":False},
                    {"action":"Cluster WAITs by signal bar and test candidate entry using future tick/M1 path, costs and execution constraints",
                     "status":"RESEARCH_ONLY","live_change":False},
                    {"action":"Compare proposed filters on separated historical holdout and forward shadow validation",
                     "status":"RESEARCH_ONLY","live_change":False}],
                "limitations":["Outcomes filtered by received time, not authoritative broker close time.",
                  "A missing outcome does not prove skipped execution.",
                  "Decision snapshots repeat intrabar; WAIT count is not a count of independent entries.",
                  "No strategy change or self-training is performed."]}
    except Exception as exc:
        return {"available":False,"reason":"PG_READ_FAILED","detail":type(exc).__name__}
