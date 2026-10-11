"""Offline multi-timeframe context at a decision instant; never synthesizes bars.

Only completed M5/M15/H1 candles from recorded history_bars are returned.
No trading integration, inference, or database mutation.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

TIMEFRAME_SECONDS = {"M5": 300, "M15": 900, "H1": 3600}


def context_at(conn: sqlite3.Connection, *, symbol: str, decision_utc: int,
               bars_per_timeframe: int = 2) -> dict:
    if not symbol or decision_utc <= 0 or not 1 <= bars_per_timeframe <= 100:
        raise ValueError("invalid context request")
    result = {"symbol": symbol, "decision_utc": decision_utc, "timeframes": {},
              "ready": True, "mode": "OFFLINE_RESEARCH_ONLY"}
    for tf, seconds in TIMEFRAME_SECONDS.items():
        # A candle's timestamp marks its OPEN; its close is OPEN + duration.
        # Query only bars which could have closed by the decision timestamp.
        rows = conn.execute("""
            SELECT time, open, high, low, close FROM history_bars
             WHERE symbol=? AND timeframe=? AND time+?<=?
             ORDER BY time DESC LIMIT ?
        """, (symbol, tf, seconds, decision_utc, bars_per_timeframe)).fetchall()
        rows = list(reversed(rows))
        valid = len(rows) == bars_per_timeframe and all(
            int(r[0]) + seconds <= decision_utc
            and all(v is not None for v in r[1:])
            and float(r[2]) >= max(float(r[1]), float(r[4]))
            and float(r[3]) <= min(float(r[1]), float(r[4]))
            and float(r[3]) <= float(r[2])
            for r in rows
        )
        contiguous = all(int(b[0]) - int(a[0]) == seconds
                         for a, b in zip(rows, rows[1:]))
        # Latest closed candle must align to its expected slot; stale data
        # means this context is unavailable, not something to forward-fill.
        latest_expected_open = (decision_utc // seconds) * seconds - seconds
        fresh = bool(rows) and int(rows[-1][0]) == latest_expected_open
        ready = valid and contiguous and fresh
        result["timeframes"][tf] = {
            "ready": ready,
            "reason": "OK" if ready else "MISSING_STALE_OR_GAPPED_BARS",
            "bars": [{"open_utc": int(t), "open": float(o), "high": float(h),
                      "low": float(l), "close": float(c)}
                     for t, o, h, l, c in rows] if ready else [],
        }
        result["ready"] = result["ready"] and ready
    return result


def read_context(db: Path, *, symbol: str, decision_utc: int,
                 bars_per_timeframe: int = 2) -> dict:
    with sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True) as conn:
        return context_at(conn, symbol=symbol, decision_utc=decision_utc,
                          bars_per_timeframe=bars_per_timeframe)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--symbol", default="XAUUSD_l")
    parser.add_argument("--decision-utc", type=int, required=True)
    parser.add_argument("--bars", type=int, default=2)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = read_context(args.db, symbol=args.symbol,
                          decision_utc=args.decision_utc,
                          bars_per_timeframe=args.bars)
    body = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(body, encoding="utf-8")
    else:
        print(body)


if __name__ == "__main__":
    main()
