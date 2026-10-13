"""Observe-only M1/M5 ingestion from MT5 FILE_COMMON with explicit time provenance.

Raw broker timestamps are immutable in the CSV. Canonical UTC bars are stored
in a separate table; legacy history_bars is never mutated by this collector.
"""
from __future__ import annotations

import argparse
import csv
import logging
import math
import os
import sqlite3
import time
from pathlib import Path

# PostgreSQL is an optional shadow sink, not a replacement for SQLite yet.

LOG = logging.getLogger("ramon.mtf_collector")
OFFSETS = range(-12, 15)
SCHEMA = """CREATE TABLE IF NOT EXISTS mtf_utc_bars (
    symbol TEXT NOT NULL, timeframe TEXT NOT NULL,
    time INTEGER NOT NULL, broker_time INTEGER NOT NULL,
    offset_seconds INTEGER NOT NULL, open REAL NOT NULL, high REAL NOT NULL,
    low REAL NOT NULL, close REAL NOT NULL, spread_points INTEGER NOT NULL,
    PRIMARY KEY(symbol,timeframe,time)
)"""


def import_snapshot(db: Path, source: Path, symbol: str, timeframe: str,
                    offset_hours: int, now: int | None = None) -> dict[str, int]:
    if timeframe not in ("M1", "M5") or offset_hours not in OFFSETS:
        raise ValueError("invalid timeframe or UTC offset")
    now = int(time.time() if now is None else now)
    period = 60 if timeframe == "M1" else 300
    rows = []
    with source.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not set(("time", "open", "high", "low", "close", "spread_points")).issubset(reader.fieldnames or ()):
            raise ValueError("invalid CSV header")
        for item in reader:
            broker_time = int(item["time"])
            utc = broker_time - offset_hours * 3600
            o, h, l, c = (float(item[k]) for k in ("open", "high", "low", "close"))
            spread = int(item["spread_points"])
            if (utc <= 0 or utc % period != 0 or utc + period > now + 30
                    or min(o, h, l, c) <= 0 or not all(map(math.isfinite, (o, h, l, c)))
                    or l > min(o, c) or h < max(o, c) or spread < 0):
                raise ValueError(f"invalid or future/unclosed bar: {broker_time}")
            rows.append((symbol, timeframe, utc, broker_time, offset_hours * 3600,
                         o, h, l, c, spread))
    if not rows or any(rows[i][2] >= rows[i + 1][2] for i in range(len(rows) - 1)):
        raise ValueError("empty or unordered snapshot")
    with sqlite3.connect(db, timeout=30) as con:
        con.execute(SCHEMA)
        con.executemany("""INSERT INTO mtf_utc_bars
            (symbol,timeframe,time,broker_time,offset_seconds,open,high,low,close,spread_points)
            VALUES (?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(symbol,timeframe,time) DO UPDATE SET
            broker_time=excluded.broker_time, offset_seconds=excluded.offset_seconds,
            open=excluded.open, high=excluded.high, low=excluded.low,
            close=excluded.close, spread_points=excluded.spread_points""", rows)
    return {"valid": len(rows), "latest_utc": rows[-1][2], "offset_seconds": offset_hours * 3600}



def mirror_snapshot_postgres(
    source: Path, symbol: str, timeframe: str, offset_hours: int,
    dsn: str, now: int | None = None,
) -> dict[str, int]:
    """Write the same validated UTC snapshot to isolated PostgreSQL shadow tables.

    Never changes SQLite or live public logging tables. Failures are reported
    to the collector but do not change trading decisions. Requires psycopg v3.
    """
    try:
        import psycopg
    except ImportError as exc:
        raise RuntimeError("PostgreSQL shadow needs psycopg>=3 installed") from exc
    if timeframe not in ("M1", "M5") or offset_hours not in OFFSETS:
        raise ValueError("invalid timeframe or UTC offset")
    now = int(time.time() if now is None else now)
    period = 60 if timeframe == "M1" else 300
    rows = []
    with source.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not set(("time", "open", "high", "low", "close", "spread_points")).issubset(reader.fieldnames or ()):
            raise ValueError("invalid CSV header")
        for item in reader:
            broker_time = int(item["time"])
            utc = broker_time - offset_hours * 3600
            o, h, l, c = (float(item[k]) for k in ("open", "high", "low", "close"))
            spread = int(item["spread_points"])
            if (utc <= 0 or utc % period != 0 or utc + period > now + 30
                    or min(o, h, l, c) <= 0 or not all(map(math.isfinite, (o, h, l, c)))
                    or l > min(o, c) or h < max(o, c) or spread < 0):
                raise ValueError(f"invalid or future/unclosed bar: {broker_time}")
            rows.append((symbol, timeframe, utc, broker_time, offset_hours * 3600,
                         o, h, l, c, spread))
    if not rows or any(rows[i][2] >= rows[i + 1][2] for i in range(len(rows) - 1)):
        raise ValueError("empty or unordered snapshot")
    with psycopg.connect(dsn, connect_timeout=5) as conn:
        with conn.cursor() as cur:
            cur.executemany("""INSERT INTO ramon_migration.mtf_utc_bars
                (symbol,timeframe,time,broker_time,offset_seconds,open,high,low,close,spread_points)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT(symbol,timeframe,time) DO UPDATE SET
                  broker_time=EXCLUDED.broker_time,offset_seconds=EXCLUDED.offset_seconds,
                  open=EXCLUDED.open,high=EXCLUDED.high,low=EXCLUDED.low,
                  close=EXCLUDED.close,spread_points=EXCLUDED.spread_points""", rows)
    return {"valid": len(rows), "latest_utc": rows[-1][2], "offset_seconds": offset_hours * 3600}

def poll(common_files: Path, db: Path, symbol: str,
         last_seen: dict[str, tuple[int, int]], offset_hours: int = 3,\n         postgres_shadow_dsn: str = "") -> None:
    for tf in ("M1", "M5"):
        source = common_files / f"RamonLive_{symbol}_{tf}.csv"
        try:
            stat = source.stat()
        except FileNotFoundError:
            LOG.warning("MISSING %s; attach RamonM1M5Collector to MT5 chart", source)
            continue
        signature = (stat.st_mtime_ns, stat.st_size)
        if signature == last_seen.get(tf):
            continue
        try:
            result = import_snapshot(db, source, symbol, tf, offset_hours)
        except (OSError, ValueError, sqlite3.Error) as exc:
            LOG.error("IMPORT_FAILED %s: %s", tf, exc)
            continue
        if postgres_shadow_dsn:
            try:
                shadow = mirror_snapshot_postgres(
                    source, symbol, tf, offset_hours, postgres_shadow_dsn
                )
                LOG.info("POSTGRES_SHADOW_MIRRORED %s valid=%s latest_utc=%s",
                         tf, shadow["valid"], shadow["latest_utc"])
            except Exception:
                # Do not mark the CSV snapshot as seen so a later poll retries.
                LOG.exception("POSTGRES_SHADOW_FAILED %s", tf)
                continue
        last_seen[tf] = signature
        LOG.info("IMPORTED_UTC %s valid=%s latest_utc=%s offset_seconds=%s",
                 tf, result["valid"], result["latest_utc"], result["offset_seconds"])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--common-files", type=Path, required=True)
    parser.add_argument("--db", type=Path, default=Path("/data/ramon_history.sqlite3"))
    parser.add_argument("--symbol", default="XAUUSD_l")
    parser.add_argument("--interval", type=int, default=30)
    parser.add_argument("--broker-utc-offset-hours", type=int, default=3,
                        help="Explicit broker clock offset, currently verified +3; stop/reconfigure if broker DST changes")
    parser.add_argument("--postgres-shadow-dsn", default="",\n                        help="Optional psycopg DSN for isolated ramon_migration.mtf_utc_bars mirror")\n    args = parser.parse_args()\n    if args.interval < 10 or args.broker_utc_offset_hours not in OFFSETS:
        parser.error("invalid collector interval/offset")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    seen: dict[str, tuple[int, int]] = {}
    while True:
        try:
            poll(args.common_files, args.db, args.symbol, seen, args.broker_utc_offset_hours,\n                 args.postgres_shadow_dsn)
        except Exception:
            LOG.exception("Unexpected collector error")
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
