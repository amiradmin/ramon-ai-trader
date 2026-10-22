"""As-of V2 signal replay audit using saved decisions, NOT a broker/tick PnL backtest.

Only reads historical SQLite; does not connect to MT5, send orders, or
make profitability claims. Gaps and unknown entry features are counted.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
import sqlite3
from pathlib import Path


def replay(db_path, *, symbol="XAUUSD_l", start_utc=None, end_utc=None,
           max_signal_age=75, limit=100000):
    if max_signal_age < 0 or not 0 < limit <= 1000000:
        raise ValueError("invalid replay limits")
    uri = f"file:{Path(db_path).resolve()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as con:
        con.row_factory = sqlite3.Row
        columns = {r[1] for r in con.execute("PRAGMA table_info(decision_samples)")}
        required = {"captured", "symbol", "direction", "signal_bar_time"}
        if not required.issubset(columns):
            raise ValueError("missing decision_samples schema")
        choice = "final_decision" if "final_decision" in columns else "direction"
        query = (f"SELECT captured, signal_bar_time, direction, {choice} AS chosen "
                 "FROM decision_samples WHERE symbol=?")
        params = [symbol]
        if start_utc is not None:
            query += " AND captured>=?"
            params.append(start_utc)
        if end_utc is not None:
            query += " AND captured<?"
            params.append(end_utc)
        query += " ORDER BY captured ASC LIMIT ?"
        params.append(limit)
        rows = con.execute(query, params).fetchall()
    reasons = Counter()
    accepted = []
    previous_capture = -1
    for row in rows:
        captured, bar = int(row["captured"]), int(row["signal_bar_time"])
        chosen = str(row["chosen"] or "").upper()
        if captured <= previous_capture:
            reasons["duplicate_or_out_of_order"] += 1
            continue
        previous_capture = captured
        # signal_bar_time is the completed M15 opening timestamp; its
        # closing edge must precede the request capture.
        if bar < 0 or bar + 900 > captured:
            reasons["future_or_incomplete_bar"] += 1
        elif captured - (bar + 900) > max_signal_age:
            reasons["stale_signal_bar"] += 1
        elif chosen not in ("BUY", "SELL"):
            reasons["wait_or_unknown"] += 1
        else:
            accepted.append({"captured_utc": captured, "signal_bar_time": bar, "side": chosen})
    return {
        "mode": "OFFLINE_ASOF_AUDIT", "broker_trades": False,
        "profit_claim": False, "symbol": symbol,
        "rows": len(rows), "causal_signals": len(accepted),
        "rejected_reasons": dict(reasons), "signals": accepted,
        "note": "No tick spread, fills, SL, margin or portfolio exposure modeled. "
                "As-of signal extraction cannot establish live EA risk safety.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="data/ramon_history.sqlite3")
    parser.add_argument("--symbol", default="XAUUSD_l")
    parser.add_argument("--start-utc", type=int)
    parser.add_argument("--end-utc", type=int)
    parser.add_argument("--max-signal-age", type=int, default=75)
    parser.add_argument("--limit", type=int, default=100000)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = replay(args.db, symbol=args.symbol, start_utc=args.start_utc,
                    end_utc=args.end_utc, max_signal_age=args.max_signal_age,
                    limit=args.limit)
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)


if __name__ == "__main__":
    main()
