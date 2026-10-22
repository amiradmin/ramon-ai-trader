"""Daily research report for Reversal. Reads market DB; writes JSON only.

Never changes EA, positions, model weights, live orders or entry gates.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timedelta, timezone
import json
import logging
import os
from pathlib import Path
import sqlite3
import time
from .reversal_regime_audit import evaluate

LOG = logging.getLogger("ramon.reversal_daily_report")


def next_run_utc(now: datetime, hour: int) -> datetime:
    t = now.astimezone(timezone.utc)
    scheduled = t.replace(hour=hour, minute=0, second=0, microsecond=0)
    return scheduled if scheduled > t else scheduled + timedelta(days=1)


def build_report(db: Path, *, symbol: str, hour: int = 7) -> dict:
    now = datetime.now(timezone.utc)
    freshness = {}
    if os.environ.get("RAMON_STORAGE_BACKEND", "sqlite").lower() == "postgres":
        import psycopg
        with psycopg.connect(os.environ["RAMON_PG_DSN"], connect_timeout=5,
                             options="-c default_transaction_read_only=on -c statement_timeout=5000") as con:
            with con.cursor() as cur:
                cur.execute("""SELECT timeframe,MAX(time),COUNT(*) FROM ramon.mtf_utc_bars
                    WHERE symbol=%s AND timeframe IN ('M1','M5') GROUP BY timeframe""", (symbol,))
                observed = {tf: (latest, count) for tf, latest, count in cur.fetchall()}
    else:
        with sqlite3.connect(f"file:{db.resolve()}?mode=ro", uri=True) as con:
            observed = {tf: con.execute(
                "SELECT MAX(time),COUNT(*) FROM mtf_utc_bars WHERE symbol=? AND timeframe=?",
                (symbol, tf)).fetchone() for tf in ("M1", "M5")}
    for tf, threshold in (("M1", 180), ("M5", 900)):
        latest, count = observed.get(tf, (None, 0))
        age = int(now.timestamp()) - int(latest) if latest is not None else None
        freshness[tf] = {
            "bars": int(count), "last_utc": int(latest) if latest is not None else None,
            "age_seconds": age,
            "status": ("MISSING" if age is None else "CLOCK_SKEW" if age < 0 else
                       "READY" if age <= threshold else "STALE")}
    audit = evaluate(db, symbol=symbol)
    return {"generated_utc": now.isoformat(), "symbol": symbol,
            "purpose": "RESEARCH_ONLY", "execution_enabled": False,
            "data_quality": freshness, "reversal_audit": audit,
            "report_state": ("INCOMPLETE_DATA" if any(x["status"] != "READY" for x in freshness.values())
                             else audit["status"]), "schedule_hour_utc": hour}


def write_report(output_dir: Path, report: dict) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    date = report["generated_utc"][:10]
    path = output_dir / f"reversal-{date}.json"
    serialized = json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    for target in (path, output_dir / "latest.json"):
        temp = target.with_suffix(target.suffix + ".tmp")
        temp.write_text(serialized, encoding="utf-8")
        os.replace(temp, target)
    return path


def main() -> None:
    p=argparse.ArgumentParser()
    p.add_argument("--db",type=Path,default=Path("/data/ramon_history.sqlite3"))
    p.add_argument("--out",type=Path,default=Path("/data/reports/reversal"))
    p.add_argument("--symbol",default="XAUUSD_l")
    p.add_argument("--hour-utc",type=int,default=7)
    p.add_argument("--once",action="store_true")
    a=p.parse_args()
    if not 0 <= a.hour_utc < 24:
        p.error("hour-utc must be between 0 and 23")
    logging.basicConfig(level=logging.INFO,format="%(asctime)s %(levelname)s %(message)s")
    if a.once:
        result=build_report(a.db,symbol=a.symbol,hour=a.hour_utc)
        print(write_report(a.out,result))
        return
    # Run on start, then at fixed UTC time daily. A failure is retried on the
    # next scheduled iteration; production DB is never modified.
    while True:
        try:
            result=build_report(a.db,symbol=a.symbol,hour=a.hour_utc)
            path=write_report(a.out,result)
            LOG.info("DAILY_REPORT %s status=%s",path,result["report_state"])
        except (OSError,sqlite3.Error,ValueError,KeyError):
            LOG.exception("DAILY_REPORT failed")
        wait=(next_run_utc(datetime.now(timezone.utc),a.hour_utc) -
              datetime.now(timezone.utc)).total_seconds()
        time.sleep(max(10.0,wait))


if __name__=="__main__":
    main()
