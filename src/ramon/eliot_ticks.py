"""Independent Eliot broker-tick import and quote-accurate exit simulation."""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
import json
from math import isfinite
from pathlib import Path
import sqlite3
from statistics import median


@dataclass(frozen=True)
class Tick:
    time_msc: int
    bid: float
    ask: float


def import_ticks(path: str | Path, db: str | Path, *, symbol: str) -> dict[str, int]:
    """Upsert bid/ask quote changes; preserve history over repeated exports."""
    target = Path(db)
    target.parent.mkdir(parents=True, exist_ok=True)
    read = valid = skipped = 0
    with sqlite3.connect(target) as con:
        con.execute("""CREATE TABLE IF NOT EXISTS eliot_ticks (
            symbol TEXT NOT NULL, time_msc INTEGER NOT NULL,
            bid REAL NOT NULL, ask REAL NOT NULL, flags INTEGER NOT NULL,
            PRIMARY KEY(symbol,time_msc,bid,ask))""")
        con.execute("CREATE INDEX IF NOT EXISTS eliot_ticks_by_time "
                    "ON eliot_ticks(symbol,time_msc)")
        batch: list[tuple[str, int, float, float, int]] = []
        with Path(path).open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if not reader.fieldnames or not {"time_msc", "bid", "ask", "flags"}.issubset(reader.fieldnames):
                raise ValueError("Eliot tick CSV requires time_msc,bid,ask,flags")
            for record in reader:
                read += 1
                try:
                    msc = int(record["time_msc"])
                    bid = float(record["bid"])
                    ask = float(record["ask"])
                    flags = int(record["flags"])
                    if msc < 1_500_000_000_000 or not (isfinite(bid) and isfinite(ask)):
                        raise ValueError
                    if bid <= 0 or ask <= bid or flags < 0:
                        raise ValueError
                except (ValueError, TypeError):
                    skipped += 1
                    continue
                batch.append((symbol, msc, bid, ask, flags))
                valid += 1
                if len(batch) >= 5000:
                    con.executemany("INSERT OR IGNORE INTO eliot_ticks VALUES (?,?,?,?,?)", batch)
                    batch.clear()
            if batch:
                con.executemany("INSERT OR IGNORE INTO eliot_ticks VALUES (?,?,?,?,?)", batch)
        count, first, last = con.execute(
            "SELECT COUNT(*),MIN(time_msc),MAX(time_msc) FROM eliot_ticks WHERE symbol=?",
            (symbol,),
        ).fetchone()
    return {"read": read, "valid": valid, "skipped": skipped,
            "stored": count, "first_time_msc": first, "last_time_msc": last}


def check_alignment(bar_db: str | Path, tick_db: str | Path, *, symbol: str) -> dict[str, float | int]:
    """Check MT5 tick and M5 timestamp scales using first quote near bar open."""
    with sqlite3.connect(tick_db) as ticks, sqlite3.connect(bar_db) as bars:
        low, high = ticks.execute(
            "SELECT MIN(time_msc),MAX(time_msc) FROM eliot_ticks WHERE symbol=?",
            (symbol,),
        ).fetchone()
        if low is None:
            raise ValueError("no ticks available for alignment")
        candidates = bars.execute(
            "SELECT time,open FROM history_bars WHERE symbol=? AND timeframe='M5' "
            "AND time*1000 BETWEEN ? AND ? ORDER BY time DESC LIMIT 150",
            (symbol, low, high - 300000),
        ).fetchall()
        deviations = []
        for time, open_price in candidates:
            quote = ticks.execute(
                "SELECT bid FROM eliot_ticks WHERE symbol=? AND time_msc BETWEEN ? AND ? "
                "ORDER BY time_msc LIMIT 1", (symbol, time * 1000, time * 1000 + 60000),
            ).fetchone()
            if quote:
                deviations.append(abs(float(quote[0]) - float(open_price)))
        if len(deviations) < 20:
            raise ValueError("insufficient overlapping M5 bar opens and tick quotes; check MT5 timestamp alignment")
        movement = median(deviations)
        if movement > 1.0:
            raise ValueError(f"tick/M5 opening prices disagree: median absolute gap {movement:.3f} XAUUSD")
        return {"matched_bar_opens": len(deviations),
                "median_abs_open_gap_price": round(movement, 4)}


def tick_outcome(ticks: list[Tick], *, entry_msc: int, end_msc: int,
                 side: int, units_per_price: float) -> tuple[str, float] | None:
    """Fill on the first real quote; exit BUY at bid / SELL at ask."""
    if side not in (-1, 1) or units_per_price <= 0 or end_msc <= entry_msc:
        raise ValueError("invalid tick replay parameters")
    quotes = [tick for tick in ticks if entry_msc <= tick.time_msc < end_msc]
    if not quotes or quotes[0].time_msc >= entry_msc + 300000:
        return None  # No quote within the next five-minute bar.
    first = quotes[0]
    entry = first.ask if side == 1 else first.bid
    target = entry + side * 5 / units_per_price
    stop = entry - side * 6 / units_per_price
    for quote in quotes:
        exit_price = quote.bid if side == 1 else quote.ask
        if (exit_price <= stop if side == 1 else exit_price >= stop):
            return "SL", -6.0
        if (exit_price >= target if side == 1 else exit_price <= target):
            return "TP", 5.0
    last = quotes[-1]
    exit_price = last.bid if side == 1 else last.ask
    return "TIME", side * (exit_price - entry) * units_per_price


def main() -> None:
    parser = argparse.ArgumentParser(description="Import isolated Eliot MT5 bid/ask ticks")
    parser.add_argument("csv")
    parser.add_argument("--db", default="/data/eliot_ticks.sqlite3")
    parser.add_argument("--bars-db", default="/data/ramon_history.sqlite3")
    parser.add_argument("--symbol", default="XAUUSD_l")
    args = parser.parse_args()
    result = import_ticks(args.csv, args.db, symbol=args.symbol)
    if result["valid"] > 0:
        result["alignment"] = check_alignment(args.bars_db, args.db, symbol=args.symbol)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
