"""Validate broker-exported Bid/Ask ticks before offline Ramon replay.

By default fail closed: do not publish a normalized CSV until the analyst
explicitly confirms that MT5 time_msc is aligned to UTC epoch milliseconds.
No synthetic ticks, interpolation, network access or live trading changes.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from collections import Counter
from pathlib import Path


def validate_export(rows: list[dict], *, expected_symbol: str,
                    utc_clock_confirmed: bool = False) -> tuple[list[dict], dict]:
    if not expected_symbol.strip():
        raise ValueError("symbol must be specified")
    if not utc_clock_confirmed:
        raise ValueError("UTC clock alignment must be confirmed before normalization")
    output = []
    previous = -1
    duplicate_exact = 0
    min_spread = max_spread = None
    for number, row in enumerate(rows, start=2):
        stamp = int(row["time_ms"])
        bid, ask = float(row["bid"]), float(row["ask"])
        if stamp <= 0 or not all(map(math.isfinite, (bid, ask))) or not 0 < bid < ask:
            raise ValueError(f"invalid tick at CSV line {number}")
        if stamp < previous:
            raise ValueError(f"out-of-order timestamp at CSV line {number}")
        if stamp == previous:
            if output[-1]["bid"] != bid or output[-1]["ask"] != ask:
                raise ValueError(
                    f"ambiguous same-millisecond quotes at CSV line {number}; "
                    "use a higher-resolution archive or mark interval UNKNOWN"
                )
            duplicate_exact += 1
            continue
        output.append({"time_ms": stamp, "bid": bid, "ask": ask})
        previous = stamp
        spread = ask - bid
        min_spread = spread if min_spread is None else min(min_spread, spread)
        max_spread = spread if max_spread is None else max(max_spread, spread)
    if not output:
        raise ValueError("no usable quotes")
    gaps = [b["time_ms"] - a["time_ms"] for a, b in zip(output, output[1:])]
    report = {
        "mode": "OFFLINE_VALIDATION_ONLY",
        "symbol": expected_symbol,
        "utc_clock_confirmed_by_operator": utc_clock_confirmed,
        "input_rows": len(rows),
        "unique_timestamps": len(output),
        "exact_duplicates_removed": duplicate_exact,
        "first_ms": output[0]["time_ms"],
        "last_ms": output[-1]["time_ms"],
        "largest_gap_ms": max(gaps, default=0),
        "gaps_gt_60s": sum(x > 60_000 for x in gaps),
        "min_spread_price": round(min_spread, 8),
        "max_spread_price": round(max_spread, 8),
        "warning": "Local confirmation does not independently verify broker clock provenance or tick completeness.",
    }
    return output, report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--symbol", default="XAUUSD_l")
    parser.add_argument("--confirm-utc-clock", action="store_true")
    args = parser.parse_args()
    # Avoid overwriting input or the report by accident.
    targets = [p.resolve() for p in (args.input, args.output, args.report)]
    if len(set(targets)) != 3:
        parser.error("input, output and report must be distinct paths")
    with args.input.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    normalized, report = validate_export(
        rows, expected_symbol=args.symbol,
        utc_clock_confirmed=args.confirm_utc_clock)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=("time_ms", "bid", "ask"))
        writer.writeheader()
        writer.writerows(normalized)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
