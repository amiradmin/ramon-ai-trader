"""Read-only, cost-aware evaluation of realized Ramon trade outcomes.

No broker connectivity, position sizing, order submission, or live decision hooks.
CSV fields: closed_utc,net_units,regime,session,entry_version,spread_points.
Net units MUST include fees, commissions and swap before import.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path


def session_name(timestamp: str) -> str:
    """UTC buckets; not exchange-specific daylight-saving session boundaries."""
    dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("closed_utc must be timezone-aware")
    hour = dt.astimezone(timezone.utc).hour
    if 0 <= hour < 7:
        return "UTC_00_07"
    if hour < 14:
        return "UTC_07_14"
    if hour < 21:
        return "UTC_14_21"
    return "UTC_21_24"


def summarize(values: list[float]) -> dict:
    if not values:
        return {"trades": 0, "net": 0.0, "profit_factor": None,
                "win_rate": None, "expectancy": None, "max_drawdown": None}
    wins = sum(x for x in values if x > 0)
    losses = -sum(x for x in values if x < 0)
    equity = peak = drawdown = 0.0
    for x in values:
        equity += x
        peak = max(equity, peak)
        drawdown = max(drawdown, peak - equity)
    return {
        "trades": len(values),
        "net": round(sum(values), 6),
        "profit_factor": round(wins / losses, 6) if losses else None,
        "win_rate": round(sum(x > 0 for x in values) / len(values), 6),
        "expectancy": round(sum(values) / len(values), 6),
        "max_drawdown": round(drawdown, 6),
    }


def audit(rows: list[dict]) -> dict:
    """Reject ambiguous monetary inputs; group purely for offline diagnostics."""
    seen: list[tuple[datetime, float, dict]] = []
    for row in rows:
        raw = row.get("net_units")
        if raw is None or not str(raw).strip():
            raise ValueError("missing net_units; gross profit cannot substitute for net")
        value = float(raw)
        if not math.isfinite(value):
            raise ValueError("non-finite net_units")
        ts = datetime.fromisoformat(row["closed_utc"].replace("Z", "+00:00"))
        if ts.tzinfo is None:
            raise ValueError("closed_utc must include UTC offset")
        seen.append((ts.astimezone(timezone.utc), value, row))
    seen.sort(key=lambda item: item[0])
    groups: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for ts, value, row in seen:
        groups["utc_bucket"][session_name(ts.isoformat())].append(value)
        for key in ("regime", "entry_version"):
            groups[key][row.get(key) or "UNKNOWN"].append(value)
    return {
        "disclaimer": "Offline descriptive audit only; no live promotion or causal claim.",
        "unit": "input net_units; ensure all rows use the same currency/account unit",
        "overall": summarize([v for _, v, _ in seen]),
        "by": {dimension: {k: summarize(v) for k, v in sorted(parts.items())}
               for dimension, parts in groups.items()},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv_path", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    with args.csv_path.open(newline="", encoding="utf-8-sig") as stream:
        data = audit(list(csv.DictReader(stream)))
    rendered = json.dumps(data, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)


if __name__ == "__main__":
    main()
