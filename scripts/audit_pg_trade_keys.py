#!/usr/bin/env python3
"""Read-only comparison of SQLite and PG trade identifiers (not counts)."""
from pathlib import Path
import json,sqlite3,subprocess,sys
root=Path(__file__).resolve().parent.parent
source=root/"data/ramon_history.sqlite3"
if not source.exists():sys.exit("SQLite source missing")
with sqlite3.connect(f"file:{source}?mode=ro",uri=True) as db:
    legacy={str(row[0]) for row in db.execute("SELECT trade_key FROM trade_outcomes")}
probe="""import os,psycopg,json
with psycopg.connect(os.environ['RAMON_PG_DSN'],options='-c default_transaction_read_only=on') as db:
 print('PG_KEYS_JSON='+json.dumps([row[0] for row in db.execute('SELECT trade_key FROM ramon.trade_outcomes')]))
"""
p=subprocess.run(["docker","compose","-f","compose.yaml","-f","compose.pg-all-staging.yaml","run","--rm","--no-deps","model","python","-c",probe],cwd=root,capture_output=True,text=True)
if p.returncode:sys.exit("PostgreSQL identifier query failed; inspect Docker status")
lines=[line.split("=",1)[1] for line in p.stdout.splitlines() if line.startswith("PG_KEYS_JSON=")]
if len(lines)!=1:sys.exit("PostgreSQL identifier output missing")
pg=set(map(str,json.loads(lines[0])))
print("SQLite trades:",len(legacy),"PostgreSQL trades:",len(pg))
print("SQLite-only trades:",len(legacy-pg))
for key in sorted(legacy-pg)[:25]: print("  sqlite_only",key)
print("PostgreSQL-only trades:",len(pg-legacy))
for key in sorted(pg-legacy)[:25]: print("  pg_only",key)
print("READ_ONLY - missing identifiers must be inspected before any retirement")
sys.exit(2 if legacy-pg else 0)
