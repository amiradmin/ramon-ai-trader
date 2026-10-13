#!/usr/bin/env python3
"""Independent recurring SQLite->PostgreSQL training sync; never touches live EA."""
import logging
import os
import signal
import time
from pathlib import Path
from psycopg.conninfo import make_conninfo
from ramon.postgres_training import sync_training_tables

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
running = True
def stop(*_args):
    global running
    running = False
signal.signal(signal.SIGTERM, stop)
signal.signal(signal.SIGINT, stop)

db=Path(os.environ.get("RAMON_HISTORY_DB","/data/ramon_history.sqlite3"))
password_file=Path(os.environ.get("RAMON_PG_PASSWORD_FILE","/run/secrets/postgres-password"))
interval=max(60,int(os.environ.get("RAMON_TRAINING_SYNC_INTERVAL_SECONDS","900")))
fail_wait=min(interval,60)

def run_once():
    password=password_file.read_text(encoding="utf8").strip()
    if not password: raise RuntimeError("empty PostgreSQL password file")
    dsn=make_conninfo(host=os.environ.get("RAMON_PG_HOST","postgres-logs"),
                      dbname="ramon_logs",user="ramon_logger",password=password)
    return sync_training_tables(db,dsn)

if __name__=="__main__":
    while running:
        start=time.monotonic()
        try:
            stats=run_once()
            logging.info("training snapshot synchronized: %s",stats)
            delay=max(1,interval-(time.monotonic()-start))
        except Exception:
            logging.exception("training sync failed; SQLite remains authoritative")
            delay=fail_wait
        while running and delay>0:
            step=min(1,delay)
            time.sleep(step)
            delay-=step
