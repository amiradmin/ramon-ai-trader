"""Read-only classifier for trade/decision conflicts.

This module never controls order execution.
"""
from __future__ import annotations

def classify(snapshot: str, final: str, executed: str) -> str:
    if final == "WAIT":
        return "WAIT_CONFLICT"
    if snapshot == "NONE":
        return "NO_SNAPSHOT_DIRECTION"
    if snapshot in {"BUY", "SELL"} and final == executed and final != snapshot:
        return "DIRECTION_OVERRIDE"
    return "OTHER_MISMATCH"

def summarize(rows: list[dict]) -> list[dict]:
    groups: dict[tuple[str, str], list[dict]] = {}
    for row in rows:
        group = classify(row["snapshot"], row["final"], row["executed"])
        groups.setdefault((row["source"], group), []).append(row)
    return [
        {"source": source, "kind": kind, "count": len(group),
         "net_r": round(sum(float(r["net_r"]) for r in group), 4),
         "within_90_seconds": sum(0 <= int(r["opened"]) - int(r["quote_time"]) <= 90 for r in group)}
        for (source, kind), group in sorted(groups.items())
    ]
