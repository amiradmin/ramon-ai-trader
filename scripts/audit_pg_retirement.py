#!/usr/bin/env python3
"""Read-only, fail-closed cutover audit. Never prints database credentials."""
from __future__ import annotations
import json, os, sqlite3, subprocess, sys, time, urllib.request
from pathlib import Path
ROOT=Path(__file__).resolve().parent.parent
TABLES=("history_bars","decision_samples","trade_outcomes","mtf_utc_bars")
SERVICES=("model","mtf-collector","dashboard","market-dashboard","training-dashboard","reversal-daily-report","v2-execution-health")
def run(*cmd):
    return subprocess.run(cmd,capture_output=True,text=True,check=False)
def main():
    failures=[]
    for name in SERVICES:
        p=run("docker","compose","-f","compose.yaml","-f","compose.pg-all-staging.yaml","ps","-q",name)
        cid=p.stdout.strip().splitlines()[0] if p.stdout.strip() else ""
        if not cid:
            failures.append(name+": container missing");continue
        i=run("docker","inspect","--format","{{json .Config.Env}}|{{.State.Running}}|{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}",cid)
        if i.returncode or "|" not in i.stdout:
            failures.append(name+": inspect failed: "+i.stderr.strip()[:180]);continue
        try:
            env,running,health=i.stdout.strip().rsplit("|",2)
            values=json.loads(env)
            backend=next((v.split("=",1)[1] for v in values if v.startswith("RAMON_STORAGE_BACKEND=")),None)
            pg_entry=run("docker","inspect","--format","{{json .Config.Entrypoint}}",cid).stdout
            ok=running=="true" and (health in ("healthy","none")) and "pg_service_entrypoint.sh" in pg_entry and (backend=="postgres" or "pg_service_entrypoint.sh" in pg_entry)
            print(f"SERVICE {name}: running={running} health={health} postgres_entrypoint={'pg_service_entrypoint.sh' in pg_entry}")
            if not ok: failures.append(name+": not verifiably running PG")
        except Exception:
            failures.append(name+": unparseable inspect response")
    try:
        with urllib.request.urlopen("http://127.0.0.1:8012/health",timeout=5) as resp:
            status=json.load(resp)
        print("MODEL ready=",status.get("ready"),"history_error=",bool(status.get("history_last_error")))
        if not status.get("ready") or status.get("history_last_error"):failures.append("model not ready")
    except Exception as e: failures.append("model health not accessible: "+type(e).__name__)
    # Query PostgreSQL through the already configured isolated model runner.
    # Do not require a host-exposed database port or expose a DSN in argv.
    probe = """import os,time,psycopg
with psycopg.connect(os.environ['RAMON_PG_DSN'],connect_timeout=5,options='-c default_transaction_read_only=on -c statement_timeout=8000') as db:
 with db.cursor() as cur:
  for name in ('history_bars','decision_samples','trade_outcomes','mtf_utc_bars'):
   cur.execute('SELECT count(*) FROM ramon.'+name)
   print('PG_COUNT',name,cur.fetchone()[0])
  cur.execute('SELECT max(captured) FROM ramon.decision_samples WHERE symbol=%s',('XAUUSD_l',))
  v=cur.fetchone()[0]
  print('PG_AGE decision',-1 if v is None else int(time.time())-int(v))
  for tf in ('M1','M5'):
   cur.execute('SELECT max(time) FROM ramon.mtf_utc_bars WHERE symbol=%s AND timeframe=%s',('XAUUSD_l',tf))
   v=cur.fetchone()[0]
   print('PG_AGE',tf,-1 if v is None else int(time.time())-int(v))
"""
    check=run("docker","compose","-f","compose.yaml","-f","compose.pg-all-staging.yaml",
              "run","--rm","--no-deps","model","python","-c",probe)
    if check.returncode:
        failures.append("PG probe failed (check Docker logs; password not printed)")
        print(check.stderr[-800:])
    else:
        for line in check.stdout.splitlines():
            if not line.startswith("PG_"):continue
            print(line)
            a=line.split()
            if a[0]=="PG_COUNT" and int(a[2])==0:
                failures.append(a[1]+": PG empty")
            if a[0]=="PG_AGE" and (int(a[2])<0 or int(a[2])>({"decision":300,"M1":300,"M5":1200}[a[1]])):
                failures.append(a[1]+": stale")
    old=ROOT/"data/ramon_history.sqlite3"
    if old.is_file():
        try:
            with sqlite3.connect(f"file:{old}?mode=ro",uri=True) as db:
                for name in TABLES:
                    try:
                        n=db.execute(f"SELECT count(*) FROM {name}").fetchone()[0]
                        print(f"SQLITE ARCHIVE {name}: {n} (snapshot counts NOT strict parity)")
                    except sqlite3.Error:
                        print(f"SQLITE ARCHIVE {name}: missing")
        except sqlite3.Error as e: failures.append("SQLite audit unavailable: "+type(e).__name__)
    else:
        print("SQLite archive missing; no parity proof")
        failures.append("SQLite archive missing")
    print("RESULT:", "NOT_APPROVED_FOR_SQLITE_RETIREMENT" if failures else "BASIC_LIVE_CHECKS_PASS_STILL_REQUIRES_TRADE_PARITY")
    for failure in failures:print("BLOCKER:",failure)
    print("NOTE: count equality alone cannot certify trade/outbox/inference parity or authorize deletion.")
    return 2 if failures else 0
if __name__=="__main__":sys.exit(main())
