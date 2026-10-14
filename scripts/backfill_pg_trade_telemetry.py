#!/usr/bin/env python3
"""Backfill ONLY missing PG trade telemetry from exact-key SQLite records.

Default is read-only dry-run. --apply explicitly enables a single transaction.
No inserts/deletes, no order execution, no P/L or identity modifications.
"""
import argparse
import json
import os
import sqlite3
from pathlib import Path

from psycopg import connect
from psycopg.types.json import Jsonb

FIELDS = ("entry_ea_version", "entry_source", "trade_role", "exit_detail",
          "closed_utc_offset_seconds", "risk_budget_units",
          "min_lot_override_used", "max_executable_risk_usd")


def build_patch(source, destination):
    patch = {}
    for field in FIELDS:
        value = source.get(field)
        if value is None:
            continue
        existing = destination.get(field)
        if existing is None:
            patch[field] = value
        elif str(existing) != str(value):
            raise ValueError("conflicting telemetry field: " + field)
    return patch


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--sqlite", type=Path, default=Path("/data/ramon_history.sqlite3"))
    p.add_argument("--symbol", default="XAUUSD_l")
    p.add_argument("--apply", action="store_true")
    a = p.parse_args()
    if not a.sqlite.is_file():
        p.error("SQLite database is not available")
    dsn = os.getenv("RAMON_PG_DSN")
    if not dsn:
        p.error("RAMON_PG_DSN not configured")
    with sqlite3.connect(a.sqlite.resolve().as_uri()+"?mode=ro", uri=True) as src:
        src.row_factory = sqlite3.Row
        src.execute("PRAGMA query_only=ON")
        cols = {r[1] for r in src.execute("PRAGMA table_info(trade_outcomes)")}
        fields = tuple(f for f in FIELDS if f in cols)
        selected = ",".join(("trade_key","sample_key",*fields))
        source = {r["trade_key"]:dict(r) for r in src.execute(
            f"SELECT {selected} FROM trade_outcomes WHERE symbol=?", (a.symbol,))}
    stats = {f:0 for f in FIELDS}
    candidates = 0
    conflict_count = 0
    with connect(dsn,connect_timeout=5,options="-c statement_timeout=15000") as con:
        if not a.apply:
            con.execute("SET TRANSACTION READ ONLY")
        with con.cursor() as cur:
            cur.execute("""SELECT trade_key,sample_key,telemetry FROM ramon.trade_outcomes
                           WHERE symbol=%s ORDER BY trade_key""" + (" FOR UPDATE" if a.apply else ""), (a.symbol,))
            pg = cur.fetchall()
            patches=[]
            for key,sample_key,telemetry in pg:
                old=source.get(key)
                if old is None: continue
                if old["sample_key"]!=sample_key:
                    conflict_count+=1
                    continue
                current=telemetry or {}
                try:
                    patch=build_patch(old,current)
                except ValueError:
                    conflict_count+=1
                    continue
                if patch:
                    candidates+=1
                    for field in patch:stats[field]+=1
                    patches.append((key,sample_key,current,patch))
            print(json.dumps({"mode":"APPLY" if a.apply else "DRY_RUN",
                "matched_pg_source":sum(k in source for k,_,_ in pg),
                "candidate_trades":candidates,"field_updates":stats,
                "conflicts":conflict_count,"sqlite_only":len(set(source)-{r[0] for r in pg}),
                "pg_only":sum(k not in source for k,_,_ in pg)},
                ensure_ascii=False,indent=2))
            if conflict_count:
                raise SystemExit("BLOCK: conflicting sample keys or telemetry; no changes committed")
            if not a.apply:
                print("DRY_RUN_ONLY: add --apply after manual review")
                return
            for key,sample_key,current,patch in patches:
                # Conditional predicate prevents overwriting PG updates arriving during backfill.
                cur.execute("""UPDATE ramon.trade_outcomes
                    SET telemetry = telemetry || %s
                    WHERE trade_key=%s AND sample_key=%s AND telemetry=%s
                    RETURNING trade_key""", (Jsonb(patch),key,sample_key,Jsonb(current)))
                if cur.fetchone() is None:
                    raise RuntimeError("concurrent telemetry change; full rollback")
        print("UPDATED_TRADES",candidates)
    print("COMMITTED" if a.apply else "DRY_RUN")


if __name__=="__main__":
    main()
