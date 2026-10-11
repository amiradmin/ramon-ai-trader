"""Offline M5/M15/H1 context using a separate, verified H1 CSV overlay.

No mutation of production history_bars; fails closed on missing, duplicate,
stale, future or invalid candles. Not connected to live trade decisions.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sqlite3
from pathlib import Path

from .multitimeframe_context_audit import context_at


def load_research_h1(path: Path) -> dict[int, tuple]:
    h1 = {}
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        required = {"time", "open", "high", "low", "close", "source"}
        if not required.issubset(reader.fieldnames or []):
            raise ValueError("invalid constructed H1 header")
        for row in reader:
            if row["source"] != "M15_AGGREGATED_RESEARCH_ONLY":
                raise ValueError("unverified H1 provenance")
            stamp = int(row["time"])
            o, h, l, c = [float(row[k]) for k in ("open", "high", "low", "close")]
            if (stamp < 0 or stamp % 3600 or stamp in h1
                or not all(math.isfinite(v) and v > 0 for v in (o, h, l, c))
                or h < max(o, c) or l > min(o, c) or l > h):
                raise ValueError("invalid or duplicate H1 candle")
            h1[stamp] = (stamp, o, h, l, c)
    return h1


def context_with_overlay(conn: sqlite3.Connection, *, symbol: str,
                         decision_utc: int, h1: dict[int, tuple],
                         bars_per_timeframe: int = 2) -> dict:
    # Existing native check is reused for M5 and M15, leaving the research H1
    # in a separate CSV instead of silently inserting it into SQLite.
    result = context_at(conn, symbol=symbol, decision_utc=decision_utc,
                        bars_per_timeframe=bars_per_timeframe)
    last_open = (decision_utc // 3600) * 3600 - 3600
    stamps = [last_open - 3600 * i for i in range(bars_per_timeframe - 1, -1, -1)]
    rows = [h1.get(ts) for ts in stamps]
    ready = all(row is not None and row[0] + 3600 <= decision_utc for row in rows)
    result["timeframes"]["H1"] = {
        "ready": ready,
        "reason": "OK" if ready else "MISSING_STALE_OR_GAPPED_CONSTRUCTED_H1",
        "source": "M15_AGGREGATED_RESEARCH_ONLY",
        "bars": ([{"open_utc": int(t), "open": o, "high": high,
                   "low": low, "close": close}
                  for t, o, high, low, close in rows] if ready else []),
    }
    result["ready"] = all(v["ready"] for v in result["timeframes"].values())
    result["mode"] = "OFFLINE_CONTEXT_WITH_CONSTRUCTED_H1_ONLY"
    result["warning"] = "Not a native broker H1 feed; do not connect to live trading."
    return result


def read_context_with_overlay(db: Path, h1_csv: Path, *,
                              symbol: str, decision_utc: int,
                              bars_per_timeframe: int = 2) -> dict:
    h1 = load_research_h1(h1_csv)
    with sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True) as conn:
        return context_with_overlay(conn, symbol=symbol, decision_utc=decision_utc,
                                    h1=h1, bars_per_timeframe=bars_per_timeframe)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--h1-csv", type=Path, required=True)
    parser.add_argument("--symbol", default="XAUUSD_l")
    parser.add_argument("--decision-utc", type=int, required=True)
    parser.add_argument("--bars", type=int, default=2)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = read_context_with_overlay(
        args.db, args.h1_csv, symbol=args.symbol,
        decision_utc=args.decision_utc, bars_per_timeframe=args.bars)
    body = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(body, encoding="utf-8")
    else:
        print(body)


if __name__ == "__main__":
    main()
