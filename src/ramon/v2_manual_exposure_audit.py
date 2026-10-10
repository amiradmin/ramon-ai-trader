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
        src=str(row["entry_source"] or "").upper()
        magic=row["entry_magic"]
        if src in {"MANUAL", "CLIENT", "MOBILE", "WEB", "MANUAL_MT5"} or magic == 0:
            return "MANUAL"
        if src in {"EA", "EXPERT", "RAMON", "AUTOMATED"} or magic in (26092212,26092213):
            return "RAMON_EA"
        return "UNKNOWN"

    events=[]
    categories=Counter()
    net_by_source=Counter()
    for row in rows:
        group=classify(row)
        categories[group]+=1
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
        if counts["MANUAL"]>0 and counts["RAMON_EA"]>0:
            max_mixed=max(max_mixed,total)
    return {
        "source": "historical_closed_trades_only",
        "symbol": symbol,
        "trades": len(rows),
        "classification": dict(categories),
        "net_units_by_source": {key:round(value,2) for key,value in net_by_source.items()},
        "max_concurrent_closed_trade_records": max_open,
        "max_mixed_manual_and_ea_concurrent": max_mixed,
        "live_positions_included": False,
        "pending_orders_included": False,
        "note": "Historical entry provenance may be incomplete; never exclude manual trades from live MT5 account-level risk.",
    }


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db",default="data/ramon_history.sqlite3")
    p.add_argument("--symbol",default="XAUUSD_l")
    args=p.parse_args()
    print(json.dumps(audit(args.db,symbol=args.symbol),ensure_ascii=False,indent=2))


if __name__=="__main__":
    main()
