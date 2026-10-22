"""Read-only, observational Bid/Ask audit after Ramon early exits.

Never treats quotes as executable fills or calculates hypothetical P&L.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import json
import math
from pathlib import Path

from .early_exit_replay_validator import validate

MAX_TICK_BYTES = 60_000_000
MAX_ROWS = 2_000_000


def _finite(value):
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def _audit_file(path, start_ms, end_ms, close_ms, direction):
    if not path.is_file():
        return {"status": "TICK_FILE_MISSING", "observed_path": None}
    if path.stat().st_size > MAX_TICK_BYTES:
        return {"status": "TICK_FILE_TOO_LARGE", "observed_path": None}
    total = 0
    selected = []
    bad = 0
    duplicates = 0
    previous_ms = None
    try:
        with path.open(newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            if not {"time_ms", "bid", "ask"}.issubset(reader.fieldnames or []):
                return {"status": "INVALID_TICK_COLUMNS", "observed_path": None}
            for record in reader:
                total += 1
                if total > MAX_ROWS:
                    return {"status": "TICK_ROW_LIMIT", "observed_path": None}
                try:
                    ms = int(record["time_ms"])
                except (TypeError, ValueError):
                    bad += 1
                    continue
                bid, ask = _finite(record["bid"]), _finite(record["ask"])
                if bid is None or ask is None or bid <= 0 or ask < bid:
                    bad += 1
                    continue
                if previous_ms is not None:
                    if ms < previous_ms:
                        return {"status": "UNSORTED_TICKS", "observed_path": None}
                    if ms == previous_ms:
                        duplicates += 1
                previous_ms = ms
                if start_ms <= ms <= end_ms:
                    selected.append((ms, bid, ask))
    except (OSError, UnicodeError, csv.Error) as exc:
        return {"status": "TICK_READ_FAILED", "error_type": type(exc).__name__,
                "observed_path": None}

    if not selected:
        return {"status": "NO_TICKS_IN_WINDOW", "file_rows": total,
                "observed_path": None}
    # Quote at/after the broker close is only a reference, not a verified fill.
    before = [r for r in selected if r[0] <= close_ms]
    after = [r for r in selected if r[0] >= close_ms]
    if not after:
        return {"status": "NO_TICKS_AFTER_EXIT", "observed_path": None}
    ref = after[0]
    side = direction.upper()
    if side not in ("BUY", "SELL"):
        return {"status": "INVALID_DIRECTION", "observed_path": None}
    idx = 1 if side == "BUY" else 2
    prices = [r[idx] for r in after]
    ref_price = ref[idx]
    movements = [(v-ref_price) if side == "BUY" else (ref_price-v) for v in prices]
    max_gap = max((b[0]-a[0] for a,b in zip(after,after[1:])), default=0)
    first_lag = ref[0]-close_ms
    last_gap = end_ms-after[-1][0]
    # Observational quality threshold. This is NOT verified broker execution.
    sufficient = first_lag <= 10_000 and last_gap <= 10_000 and max_gap <= 60_000 and bad == 0 and duplicates == 0
    return {
        "status": "OBSERVED_CONTINUOUS_QUOTES" if sufficient else "QUOTE_COVERAGE_UNVERIFIED",
        "file_rows": total, "window_ticks": len(selected),
        "after_exit_ticks": len(after), "malformed_quotes": bad,
        "duplicate_timestamps": duplicates,
        "first_quote_lag_ms": first_lag, "last_quote_gap_ms": last_gap,
        "largest_intertick_gap_ms": max_gap,
        "observed_path": {
            "reference_time_ms": ref[0], "reference_side": "bid" if side=="BUY" else "ask",
            "reference_quote": round(ref_price, 5),
            "last_quote": round(prices[-1], 5),
            "max_favorable_price_move": round(max(movements), 5),
            "max_adverse_price_move": round(max(0, -min(movements)), 5),
            "last_directional_price_move": round(movements[-1], 5),
            "price_unit": "XAUUSD_l quote price, not account money",
            "interpretation": "OBSERVED_QUOTE_PATH_ONLY_NOT_EXECUTABLE_PNL",
        },
    }


def audit(db_path, tick_dir, symbol="XAUUSD_l", lookahead_minutes=60):
    base = validate(db_path, symbol=symbol, lookahead_minutes=lookahead_minutes)
    if not base.get("available"):
        return base
    folder = Path(tick_dir).expanduser().resolve()
    results = []
    for trade in base["trades"]:
        row = dict(trade)
        closed = _finite(row.get("closed"))
        if closed is None or closed <= 0:
            row["tick_audit"] = {"status": "INVALID_CLOSE_TIME", "observed_path": None}
        else:
            day = datetime.fromtimestamp(closed, timezone.utc).strftime("%Y%m%d")
            filename = f"Ramon_EarlyExit_{symbol}_{day}_Ticks.csv"
            # Resolve from a constant basename only, with no caller-provided path traversal.
            row["tick_audit"] = _audit_file(
                folder / filename, int(closed*1000),
                int((closed+lookahead_minutes*60)*1000), int(closed*1000),
                row.get("direction") or "")
        results.append(row)
    return {
        "available": True, "read_only": True, "symbol": symbol,
        "tick_directory": str(folder), "candidate_trades": len(results),
        "lookahead_minutes": lookahead_minutes,
        "trades": results,
        "counterfactual_status": "NOT_EVALUATED",
        "execution_evidence": "UNVERIFIED_BROKER_SL_TP_FILLS_FEES",
        "policy": "Observational bid/ask quote movements only; no executable PnL or policy changes",
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="data/ramon_history.sqlite3")
    parser.add_argument("--ticks-dir", default="data/imports/early_exit_ticks")
    parser.add_argument("--symbol", default="XAUUSD_l")
    parser.add_argument("--lookahead-minutes", type=int, default=60)
    args = parser.parse_args()
    if args.symbol != "XAUUSD_l":
        parser.error("only XAUUSD_l research exports supported")
    if not 1 <= args.lookahead_minutes <= 240:
        parser.error("lookahead-minutes must be 1..240")
    print(json.dumps(audit(args.db, args.ticks_dir, args.symbol, args.lookahead_minutes),
                     ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
