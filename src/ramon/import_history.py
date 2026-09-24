from __future__ import annotations

import argparse
import csv
from pathlib import Path
import sqlite3

from .history import ensure_history_db


REQUIRED = ("time", "open", "high", "low", "close")


def import_csv(db: str | Path, csv_path: str | Path, *, symbol: str, timeframe: str = "M15") -> dict[str, int]:
    """Import MT5-exported closed bars using MT5 epoch seconds without timezone conversion."""
    if timeframe != "M15":
        raise ValueError("only M15 history is supported")
    if not symbol.upper().startswith("XAUUSD"):
        raise ValueError("only XAUUSD variants are supported")

    source = Path(csv_path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(source)

    path = ensure_history_db(db)
    rows: list[tuple[object, ...]] = []
    skipped = 0
    with source.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError("CSV has no header")
        normalized = {name.strip().lower(): name for name in reader.fieldnames}
        missing = [name for name in REQUIRED if name not in normalized]
        if missing:
            raise ValueError(f"CSV missing columns: {','.join(missing)}")
        spread_name = normalized.get("spread_points") or normalized.get("spread")
        for raw in reader:
            try:
                timestamp = int(float(raw[normalized["time"]]))
                open_ = float(raw[normalized["open"]])
                high = float(raw[normalized["high"]])
                low = float(raw[normalized["low"]])
                close = float(raw[normalized["close"]])
                spread = int(float(raw[spread_name])) if spread_name and raw.get(spread_name) not in {None, ""} else 0
                if timestamp <= 0 or min(open_, high, low, close) <= 0:
                    raise ValueError
                if low > min(open_, close) or high < max(open_, close):
                    raise ValueError
            except (TypeError, ValueError):
                skipped += 1
                continue
            rows.append((symbol, timeframe, timestamp, open_, high, low, close, spread))

    if not rows:
        raise ValueError("CSV contains no valid bars")

    with sqlite3.connect(path, timeout=30) as conn:
        before = conn.total_changes
        conn.executemany(
            """
            INSERT INTO history_bars(symbol,timeframe,time,open,high,low,close,spread_points)
            VALUES (?,?,?,?,?,?,?,?)
            ON CONFLICT(symbol,timeframe,time) DO UPDATE SET
                open=excluded.open,
                high=excluded.high,
                low=excluded.low,
                close=excluded.close,
                spread_points=CASE
                    WHEN excluded.spread_points>0 THEN excluded.spread_points
                    ELSE history_bars.spread_points
                END
            """,
            rows,
        )
        changed = conn.total_changes - before
        total = conn.execute(
            "SELECT COUNT(*) FROM history_bars WHERE symbol=? AND timeframe=?",
            (symbol, timeframe),
        ).fetchone()[0]

    return {"read": len(rows) + skipped, "valid": len(rows), "skipped": skipped, "changed": changed, "total": int(total)}


def main() -> None:
    parser = argparse.ArgumentParser(description="Import MT5 closed M15 bars into Ramon SQLite history")
    parser.add_argument("csv")
    parser.add_argument("--db", default="/data/ramon_history.sqlite3")
    parser.add_argument("--symbol", default="XAUUSD_l")
    parser.add_argument("--timeframe", default="M15")
    args = parser.parse_args()
    print(import_csv(args.db, args.csv, symbol=args.symbol, timeframe=args.timeframe))


if __name__ == "__main__":
    main()
