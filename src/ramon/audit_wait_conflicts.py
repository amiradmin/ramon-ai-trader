"""Research-only WAIT/conflicting-direction audit. Never changes live execution."""
from __future__ import annotations
import argparse
import json
import os
from collections import defaultdict
from datetime import datetime, timezone

SQL = """
SELECT s.sample_key, s.source->>'direction' AS snapshot,
 s.source->>'final_decision' AS final_decision,
 t.source->>'direction' AS executed,
 COALESCE(t.source->>'entry_source','UNKNOWN') AS source,
 (s.source->>'quote_time')::bigint AS quote_time,
 (t.source->>'opened')::bigint AS opened,
 (t.source->>'closed')::bigint AS closed,
 (t.source->>'net_r')::double precision AS net_r
FROM ramon_migration.training_trade_outcomes t
JOIN ramon_migration.training_decision_samples s
 ON s.sample_key = t.source->>'sample_key'
WHERE s.source->>'symbol'=%s
 AND t.source->>'symbol'=s.source->>'symbol'
 AND s.source->>'chronos_model'=%s
 AND t.source->>'training_status'='LEARNABLE'
 AND t.source->>'direction' IS DISTINCT FROM s.source->>'direction'
ORDER BY (s.source->>'quote_time')::bigint DESC, s.sample_key
"""

def category(snapshot, final, executed):
    if final == "WAIT":
        return "WAIT_CONFLICT"
    if snapshot in ("BUY", "SELL") and final in ("BUY", "SELL") and final == executed:
        return "DIRECTION_OVERRIDE"
    if snapshot == "NONE":
        return "NO_SNAPSHOT_DIRECTION"
    return "OTHER_DIRECTION_MISMATCH"

def summarize(rows, recent=15):
    groups = defaultdict(list)
    recent_rows = []
    for r in rows:
        source = r["source"]
        group = category(r["snapshot"], r["final_decision"], r["executed"])
        delay = r["opened"]-r["quote_time"]
        groups[(source,group)].append(r)
        if len(recent_rows)<recent:
            recent_rows.append({
                "quote_time_utc":datetime.fromtimestamp(r["quote_time"], timezone.utc).isoformat(),
                "source":source, "classification":group,
                "snapshot":r["snapshot"],"final":r["final_decision"],
                "executed":r["executed"], "delay_seconds":delay,
                "net_r":round(r["net_r"],4)
            })
    summary=[]
    for (source,group),items in sorted(groups.items()):
        net=sum(r["net_r"] for r in items)
        summary.append({
            "source":source,"classification":group,"trades":len(items),
            "total_r":round(net,4),"average_r":round(net/len(items),4),
            "within_90_seconds":sum(0<=r["opened"]-r["quote_time"]<=90 for r in items)
        })
    return {"research_only":True,"execution_enabled":False,
            "changes_live_logic":False,"direction_mismatch_trades":len(rows),
            "summary":summary,"recent":recent_rows,
            "warning":"Observational executed trades only: no causal estimate of alternative actions. Do not automatically relabel or promote."}

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--symbol",default="XAUUSD_l")
    p.add_argument("--chronos-model",default="autogluon/chronos-2-small")
    p.add_argument("--dsn",default=os.getenv("RAMON_PG_TRAINING_DSN",""))
    p.add_argument("--recent",type=int,default=15)
    a=p.parse_args()
    if not a.dsn:
        p.error("RAMON_PG_TRAINING_DSN is required; never embed credentials in code")
    if not 0 <= a.recent <=100:
        p.error("--recent must be between 0 and 100")
    import psycopg
    with psycopg.connect(a.dsn,connect_timeout=5) as con:
        with con.cursor() as cur:
            cur.execute("BEGIN TRANSACTION READ ONLY")
            cur.execute("SET LOCAL statement_timeout='10000ms'")
            cur.execute(SQL,(a.symbol,a.chronos_model))
            cols=[x.name for x in cur.description]
            rows=[dict(zip(cols,x)) for x in cur.fetchall()]
    print(json.dumps(summarize(rows,a.recent),ensure_ascii=False,indent=2))

if __name__=="__main__":
    main()
