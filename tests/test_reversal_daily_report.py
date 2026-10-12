from datetime import datetime, timezone
import json
import sqlite3

from ramon.reversal_daily_report import build_report, next_run_utc, write_report


def test_schedule_is_daily_utc():
    at=datetime(2026,10,12,7,1,tzinfo=timezone.utc)
    assert next_run_utc(at,7).isoformat()=="2026-10-13T07:00:00+00:00"


def test_daily_report_does_not_mutate_market_tables(tmp_path):
    db=tmp_path/"history.sqlite3"
    with sqlite3.connect(db) as con:
        con.execute("""CREATE TABLE mtf_utc_bars(
            symbol TEXT,timeframe TEXT,time INTEGER,close REAL,spread_points INTEGER)""")
        con.execute("INSERT INTO mtf_utc_bars VALUES(?,?,?,?,?)",
                    ("XAUUSD_l","M1",1791786600,4200.0,42))
        before=con.execute("SELECT COUNT(*) FROM mtf_utc_bars").fetchone()[0]
    report=build_report(db,symbol="XAUUSD_l")
    assert report["purpose"]=="RESEARCH_ONLY"
    assert report["execution_enabled"] is False
    assert report["reversal_audit"]["status"]=="INSUFFICIENT_HISTORY"
    assert report["data_quality"]["M5"]["status"]=="MISSING"
    output=tmp_path/"out"
    file=write_report(output,report)
    assert file.is_file()
    assert json.loads((output/"latest.json").read_text())["execution_enabled"] is False
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT COUNT(*) FROM mtf_utc_bars").fetchone()[0]==before
