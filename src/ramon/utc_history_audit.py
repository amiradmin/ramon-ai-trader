"""Build read-only canonical UTC market-bar views without rewriting legacy rows.

MT5 exports can carry broker-wall-clock epochs; mtf_utc_bars stores verified
canonical UTC and preserves broker_time/offset_seconds. Never silently infer
offsets for historic M15 rows or mutate live learning/trade tables.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


def inspect(con: sqlite3.Connection) -> dict:
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not {"history_bars", "mtf_utc_bars"} <= tables:
        raise ValueError("history_bars and mtf_utc_bars are required")
    report = {}
    for tf in ("M1", "M5"):
        matches = []
        for hours in range(-12, 15):
            offset = hours * 3600
            n, same = con.execute("""
                SELECT count(*),sum(CASE WHEN abs(h.open-m.open)<0.000001
                  AND abs(h.high-m.high)<0.000001
                  AND abs(h.low-m.low)<0.000001
                  AND abs(h.close-m.close)<0.000001 THEN 1 ELSE 0 END)
                FROM history_bars h JOIN mtf_utc_bars m
                  ON h.symbol=m.symbol AND h.timeframe=m.timeframe
                 AND h.time=m.time+?
                WHERE h.timeframe=?""", (offset, tf)).fetchone()
            matches.append({"offset_seconds": offset, "joined": n, "matching_ohlc": same or 0})
        report[tf] = sorted(matches, key=lambda x: (x["matching_ohlc"], x["joined"]), reverse=True)[:3]
    report["policy"] = ("UTC Unix epoch in canonical_utc_bars; original history_bars stays immutable. "
                        "M15 is included from its original source with explicit unverified provenance.")
    return report


VIEW_SQL = """
CREATE VIEW canonical_utc_bars AS
SELECT symbol,timeframe,time,open,high,low,close,spread_points,
       broker_time,offset_seconds,'mtf_utc_bars' AS source,
       'VERIFIED_UTC' AS time_quality
FROM mtf_utc_bars WHERE timeframe IN ('M1','M5')
UNION ALL
SELECT symbol,timeframe,time,open,high,low,close,spread_points,
       NULL AS broker_time,NULL AS offset_seconds,'history_bars' AS source,
       'UNVERIFIED_LEGACY_EPOCH' AS time_quality
FROM history_bars WHERE timeframe NOT IN ('M1','M5')
"""


def install(db: Path, apply: bool) -> dict:
    # This operation only creates a VIEW; no existing row is modified.
    uri = f"file:{db.resolve()}?mode={'rw' if apply else 'ro'}"
    with sqlite3.connect(uri, uri=True, timeout=15) as con:
        result = inspect(con)
        if apply:
            con.execute("DROP VIEW IF EXISTS canonical_utc_bars")
            con.execute(VIEW_SQL)
            con.commit()
            result["view_installed"] = True
        else:
            result["view_installed"] = False
    return result


def main() -> None:
    p = argparse.ArgumentParser(description="Audit MT5 broker-time versus UTC without rewriting history")
    p.add_argument("--db", type=Path, default=Path("data/ramon_history.sqlite3"))
    p.add_argument("--apply", action="store_true", help="Create canonical_utc_bars VIEW only")
    args = p.parse_args()
    print(json.dumps(install(args.db, args.apply), indent=2))


if __name__ == "__main__":
    main()
