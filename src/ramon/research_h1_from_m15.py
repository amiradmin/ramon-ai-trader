"""Construct research-only H1 OHLC CSV from complete M15 hours.

Source database is read-only. Missing, duplicate, misaligned or invalid M15
candles never produce an H1 candle; no gap filling or future interpolation.
This is NOT the broker's native H1 series and is never used for live orders.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sqlite3
from collections import defaultdict
from pathlib import Path


def aggregate_h1(rows):
    buckets = defaultdict(list)
    for stamp, o, h, l, c in rows:
        stamp = int(stamp)
        nums = tuple(float(v) for v in (o, h, l, c))
        buckets[(stamp // 3600) * 3600].append((stamp, *nums))
    ready, skipped = [], []
    for hour, bars in sorted(buckets.items()):
        bars.sort(key=lambda r: r[0])
        expected = [hour + 900 * i for i in range(4)]
        timestamps = [r[0] for r in bars]
        if timestamps != expected:
            skipped.append({"hour_utc": hour, "reason": "MISSING_DUPLICATE_OR_MISALIGNED_M15"})
            continue
        if any(not all(math.isfinite(x) and x > 0 for x in bar[1:])
               or bar[2] < max(bar[1], bar[4])
               or bar[3] > min(bar[1], bar[4])
               or bar[3] > bar[2] for bar in bars):
            skipped.append({"hour_utc": hour, "reason": "INVALID_M15_OHLC"})
            continue
        ready.append({
            "time": hour,
            "open": bars[0][1],
            "high": max(bar[2] for bar in bars),
            "low": min(bar[3] for bar in bars),
            "close": bars[-1][4],
            "source": "M15_AGGREGATED_RESEARCH_ONLY",
        })
    return ready, skipped


def read_m15(db: Path, symbol: str):
    if not symbol:
        raise ValueError("symbol required")
    with sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True) as conn:
        return conn.execute("""
            SELECT time,open,high,low,close FROM history_bars
            WHERE symbol=? AND timeframe='M15' ORDER BY time
        """, (symbol,)).fetchall()


def export_h1(db: Path, *, symbol: str, output: Path, report: Path):
    if output.resolve() == report.resolve():
        raise ValueError("data and report paths must differ")
    m15 = read_m15(db, symbol)
    candles, skipped = aggregate_h1(m15)
    output.parent.mkdir(parents=True, exist_ok=True)
    report.parent.mkdir(parents=True, exist_ok=True)
    # No writing to history_bars or replacing any database data.
    with output.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=[
            "time", "open", "high", "low", "close", "source"
        ])
        writer.writeheader()
        writer.writerows(candles)
    summary = {
        "mode": "OFFLINE_H1_RESEARCH_EXPORT_ONLY",
        "symbol": symbol,
        "m15_rows": len(m15),
        "hour_periods": len(candles) + len(skipped),
        "complete_h1": len(candles),
        "incomplete_or_invalid_h1": len(skipped),
        "skipped": skipped,
        "output": str(output),
        "warning": "Constructed H1 is separate from native broker H1 and is not imported into live DB.",
    }
    report.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n",
                      encoding="utf-8")
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--symbol", default="XAUUSD_l")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    result = export_h1(args.db, symbol=args.symbol,
                       output=args.output, report=args.report)
    print(json.dumps({key: value for key, value in result.items() if key != "skipped"},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
