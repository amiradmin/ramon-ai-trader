#!/usr/bin/env python3
"""Identify/optionally recover legacy SQLite-only trades in PG. No orders or service restarts."""
import argparse,json,sqlite3
from pathlib import Path
from psycopg import connect
from psycopg.types.json import Jsonb
from psycopg.conninfo import make_conninfo

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--sqlite",type=Path,required=True)
    p.add_argument("--password-file",type=Path,required=True)
    p.add_argument("--apply",action="store_true")
    a=p.parse_args()
    if not a.sqlite.is_file():p.error("source DB missing")
    with sqlite3.connect(f"file:{a.sqlite.resolve()}?mode=ro",uri=True) as src:
        src.row_factory=sqlite3.Row
        cols={row[1] for row in src.execute("PRAGMA table_info(trade_outcomes)")}
        required={'trade_key','sample_key','symbol','direction','opened','closed','net_units','initial_risk_units','net_r','exit_reason','received'}
        if not required<=cols:raise SystemExit("BLOCK: legacy fields missing: "+str(sorted(required-cols)))
        rows={row['trade_key']:dict(row) for row in src.execute('SELECT * FROM trade_outcomes')}
    dsn=make_conninfo(host="postgres-logs",port=5432,dbname="ramon_logs",user="ramon_logger",
                      password=a.password_file.read_text().strip(),connect_timeout=5)
    with connect(dsn) as pg:
        with pg.cursor() as cur:
            cur.execute("SELECT trade_key,sample_key FROM ramon.trade_outcomes")
            dest=dict(cur.fetchall())
            missing=sorted(rows.keys()-dest.keys())
            print("sqlite_only",len(missing))
            errors=[]
            for key in missing:
                row=rows[key]
                cur.execute("SELECT trade_key FROM ramon.trade_outcomes WHERE sample_key=%s",(row['sample_key'],))
                collision=cur.fetchone()
                if collision:
                    errors.append(key)
                    print("BLOCK duplicate sample_key",key,"existing=",collision[0])
                else:
                    print("READY",key,"direction=",row['direction'],"net_r=",row['net_r'])
            if errors:
                pg.rollback()
                raise SystemExit("BLOCK: sample_key collisions require manual investigation")
            if not a.apply:
                pg.rollback()
                print("DRY_RUN. No changes. Re-run with --apply after review.")
                return
            names=sorted(required)
            for key in missing:
                row=rows[key]
                telemetry={"_migration_source":"legacy_sqlite_trade_outcomes","_migration_key":key}
                # Preserve optional telemetry columns without fabricating trade data.
                for k,v in row.items():
                    if k not in required and v is not None:
                        telemetry[k]=v
                cur.execute("""INSERT INTO ramon.trade_outcomes
                (trade_key,sample_key,symbol,direction,opened,closed,net_units,initial_risk_units,net_r,exit_reason,received,telemetry)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT DO NOTHING""",
                tuple(row[n] for n in ('trade_key','sample_key','symbol','direction','opened','closed','net_units',
                                       'initial_risk_units','net_r','exit_reason','received'))+(Jsonb(telemetry),))
                if cur.rowcount!=1:
                    raise RuntimeError("BLOCK: failed to insert "+key)
            cur.execute("SELECT trade_key FROM ramon.trade_outcomes")
            after={x[0] for x in cur.fetchall()}
            if not set(missing)<=after:
                raise RuntimeError("BLOCK: key verification failed")
        print("RECOVERED",len(missing),"keys; transaction committed")
if __name__=="__main__":main()
