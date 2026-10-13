#!/usr/bin/env python3
"""Synchronize authoritative SQLite training tables to PostgreSQL (manual run)."""
from __future__ import annotations
import argparse
import json
from pathlib import Path

from psycopg.conninfo import make_conninfo
from ramon.postgres_training import sync_training_tables

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--sqlite",type=Path,default=Path("/data/ramon_history.sqlite3"))
    p.add_argument("--password-file",type=Path,default=Path("/run/secrets/postgres-password"))
    p.add_argument("--host",default="postgres-logs")
    p.add_argument("--dbname",default="ramon_logs")
    p.add_argument("--user",default="ramon_logger")
    args=p.parse_args()
    password=args.password_file.read_text(encoding="utf-8").strip()
    if not password: raise SystemExit("missing password")
    dsn=make_conninfo(host=args.host,dbname=args.dbname,user=args.user,password=password)
    print(json.dumps(sync_training_tables(args.sqlite,dsn),sort_keys=True))

if __name__=="__main__":
    main()
