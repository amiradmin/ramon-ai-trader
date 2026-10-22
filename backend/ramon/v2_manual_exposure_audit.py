"""Read-only reconciliation of overlapping Ramon and manual account exposure.

Historical closed trades are NOT a complete live-position snapshot. This audit
separates manual vs EA-origin trades when provenance is available, and reports
unknown sources explicitly. No orders or database writes.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sqlite3


def audit(db_path, *, symbol="XAUUSD_l", limit=100000):
    if not 1 <= limit <= 100000:
        raise ValueError("invalid limit")
    path = Path(db_path).resolve()
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
    try:
        con.row_factory = sqlite3.Row
        columns = {r[1] for r in con.execute("PRAGMA table_info(trade_outcomes)")}
        needed = {"symbol", "opened", "closed", "net_units", "entry_source", "entry_magic"}
        if not needed.issubset(columns):
            raise ValueError(f"missing schema: {sorted(needed-columns)}")
        rows = con.execute(
            "SELECT opened,closed,net_units,entry_source,entry_magic,trade_key "
            "FROM trade_outcomes WHERE symbol=? AND opened IS NOT NULL "
            "AND closed IS NOT NULL ORDER BY opened LIMIT ?",(symbol,limit)).fetchall()
    finally:
        con.close()

    def classify(row):
        # An EA Magic number identifies order routing, NOT whether a human
        # clicked a dashboard button. Never turn missing provenance into AUTO.
        src = str(row["entry_source"] or "").strip().upper()
        if src in {"AUTO_RAMON", "RANGE_AUTO", "EA", "AUTOMATED"}:
            return "AUTOMATED"
        if src in {"DASHBOARD_OPPORTUNITY", "MANUAL", "CLIENT", "MOBILE",
                   "WEB", "MANUAL_MT5", "HUMAN_ASSISTED"}:
            return "HUMAN_INITIATED_OR_ASSISTED"
        if not src:
            return "UNKNOWN_SOURCE"
        return "OTHER_SOURCE"

    events=[]
    categories=Counter()
    net_by_source=Counter()
    raw_sources=Counter()
    raw_nets=Counter()
    for row in rows:
        group=classify(row)
        categories[group]+=1
        raw=str(row["entry_source"] or "NULL")
        raw_sources[raw]+=1
        raw_nets[raw]+=float(row["net_units"] or 0)
        net_by_source[group]+=float(row["net_units"] or 0)
        opened,closed=int(row["opened"]),int(row["closed"])
        if closed>opened:
            events.append((opened,1,group))
            events.append((closed,-1,group))
    events.sort(key=lambda x:(x[0],x[1]))
    counts=Counter()
    max_open=0
    max_mixed=0
    for _,sign,group in events:
        counts[group]+=sign
        total=sum(counts.values())
        max_open=max(max_open,total)
        if counts["HUMAN_INITIATED_OR_ASSISTED"]>0 and counts["AUTOMATED"]>0:
            max_mixed=max(max_mixed,total)
    return {
        "source": "historical_closed_trades_only",
        "symbol": symbol,
        "trades": len(rows),
        "classification": dict(categories),
        "entry_source_counts": dict(raw_sources),
        "net_units_by_entry_source": {key:round(value,2) for key,value in raw_nets.items()},
        "net_units_by_source": {key:round(value,2) for key,value in net_by_source.items()},
        "max_concurrent_closed_trade_records": max_open,
        "max_mixed_manual_and_ea_concurrent": max_mixed,
        "live_positions_included": False,
        "pending_orders_included": False,
        "note": "Magic is not proof of automated entry. DASHBOARD_OPPORTUNITY is dashboard-origin, not necessarily a direct MT5 manual click. NULL is unknown. All exposure counts toward live account risk.",
    }


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db",default="data/ramon_history.sqlite3")
    p.add_argument("--symbol",default="XAUUSD_l")
    args=p.parse_args()
    print(json.dumps(audit(args.db,symbol=args.symbol),ensure_ascii=False,indent=2))


if __name__=="__main__":
    main()
