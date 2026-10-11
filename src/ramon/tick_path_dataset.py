"""Build offline TP/SL labels from explicitly recorded entry plans and bid/ask ticks.

CSV input is deliberately explicit: never derive an entry or SL/TP from a
forecast retrospectively. Nothing in this module issues trades or changes DB.
"""
from __future__ import annotations

import argparse
import csv
import json
from bisect import bisect_left, bisect_right
from collections import Counter
from pathlib import Path

from .tp_sl_shadow import PathRequest, Quote, first_touch


def label_paths(opportunities: list[dict], quote_rows: list[dict],
                *, max_gap_ms: int = 60_000) -> list[dict]:
    if max_gap_ms <= 0:
        raise ValueError("max_gap_ms must be positive")
    quotes = [
        Quote(time_ms=int(r["time_ms"]), bid=float(r["bid"]), ask=float(r["ask"]))
        for r in quote_rows
    ]
    times = [q.time_ms for q in quotes]
    if any(b <= a for a, b in zip(times, times[1:])):
        raise ValueError("tick archive must be strictly chronological without duplicates")
    ids = set()
    output = []
    for item in opportunities:
        key = str(item.get("opportunity_id", "")).strip()
        if not key or key in ids:
            raise ValueError("missing or duplicate opportunity_id")
        ids.add(key)
        req = PathRequest(
            direction=str(item["direction"]).upper(),
            entry_ms=int(item["entry_ms"]),
            entry_price=float(item["entry_price"]),
            stop=float(item["stop"]),
            target=float(item["target"]),
            horizon_ms=int(item["horizon_ms"]),
            max_gap_ms=max_gap_ms,
        )
        lo = bisect_left(times, req.entry_ms)
        hi = bisect_right(times, req.entry_ms + req.horizon_ms)
        outcome = first_touch(req, quotes[lo:hi])
        output.append({
            "opportunity_id": key,
            "label": outcome["label"],
            "reason": outcome["reason"],
            "touch_ms": outcome["touch_ms"],
            "entry_ms": req.entry_ms,
            "horizon_ms": req.horizon_ms,
            "quote_count_in_window": hi - lo,
        })
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--opportunities", type=Path, required=True)
    parser.add_argument("--quotes", type=Path, required=True)
    parser.add_argument("--max-gap-ms", type=int, default=60_000)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    with args.opportunities.open(encoding="utf-8-sig", newline="") as f:
        opportunities = list(csv.DictReader(f))
    with args.quotes.open(encoding="utf-8-sig", newline="") as f:
        quotes = list(csv.DictReader(f))
    labeled = label_paths(opportunities, quotes, max_gap_ms=args.max_gap_ms)
    counts = dict(sorted(Counter(row["label"] for row in labeled).items()))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(labeled[0]) if labeled else [
            "opportunity_id", "label", "reason", "touch_ms",
            "entry_ms", "horizon_ms", "quote_count_in_window",
        ])
        writer.writeheader()
        writer.writerows(labeled)
    print(json.dumps({
        "mode": "OFFLINE_LABELS_ONLY",
        "opportunities": len(labeled),
        "quote_rows": len(quotes),
        "labels": counts,
        "output": str(args.output),
        "warning": "First-touch quote label is not proof of trade execution",
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
