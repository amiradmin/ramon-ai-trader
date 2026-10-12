"""Import MT5 FILE_COMMON closed M1/M5 snapshots into Ramon SQLite.

No trading APIs. Import only complete, atomically published CSVs.
"""
from __future__ import annotations

import argparse
import logging
import time
from pathlib import Path
from .import_history import import_csv

LOG = logging.getLogger("ramon.mtf_collector")


def poll(common_files: Path, db: Path, symbol: str, last_seen: dict[str, tuple[int, int]]) -> None:
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
            result = import_csv(db, source, symbol=symbol, timeframe=tf)
        except (OSError, ValueError) as exc:
            LOG.error("IMPORT_FAILED %s: %s", tf, exc)
            continue
        last_seen[tf] = signature
        LOG.info("IMPORTED %s valid=%s skipped=%s rows=%s", tf,
                 result["valid"], result["skipped"], result["total"])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--common-files", type=Path, required=True)
    parser.add_argument("--db", type=Path, default=Path("/data/ramon_history.sqlite3"))
    parser.add_argument("--symbol", default="XAUUSD_l")
    parser.add_argument("--interval", type=int, default=30)
    args = parser.parse_args()
    if args.interval < 10:
        parser.error("interval must be >= 10 seconds")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    seen: dict[str, tuple[int, int]] = {}
    while True:
        try:
            poll(args.common_files, args.db, args.symbol, seen)
        except Exception:
            LOG.exception("Unexpected collector error")
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
