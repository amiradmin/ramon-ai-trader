"""Read-only realized-trade performance grouped by exchange-local sessions."""
from __future__ import annotations

import argparse
import json
import sqlite3
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo


SESSIONS = {
    "ASIA": (ZoneInfo("Asia/Tokyo"), 8, 16),
    "LONDON": (ZoneInfo("Europe/London"), 8, 16),
    "NEW_YORK": (ZoneInfo("America/New_York"), 8, 17),
}


def memberships(opened_utc: int) -> list[str]:
    instant = datetime.fromtimestamp(opened_utc, UTC)
    result = []
    for name, (zone, start, end) in SESSIONS.items():
        if start <= instant.astimezone(zone).hour < end:
            result.append(name)
    return result or ["OFF_SESSION"]


def summarize(rows):
    buckets = defaultdict(list)
    for row in rows:
        for name in memberships(int(row["opened"])):
            buckets[name].append(float(row["net_units"]))
    result = {}
    for name, values in sorted(buckets.items()):
        profit = sum(v for v in values if v > 0)
        loss = -sum(v for v in values if v < 0)
        result[name] = {
            "trades": len(values), "net_units": round(sum(values), 6),
            "mean_units": round(sum(values) / len(values), 6),
            "profit_factor": round(profit / loss, 6) if loss else None,
            "profitable": sum(v > 0 for v in values),
        }
    return result


def audit(db: Path, symbol: str) -> dict:
    with sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True) as con:
        con.row_factory = sqlite3.Row
        rows = con.execute("SELECT opened,net_units FROM trade_outcomes WHERE symbol=? AND opened>0 AND net_units IS NOT NULL", (symbol,)).fetchall()
    return {"mode": "READ_ONLY_SESSION_PERFORMANCE_AUDIT", "symbol": symbol,
            "sessions": summarize(rows),
            "limitations": ["Sessions overlap and are reported independently.", "DST follows IANA timezone rules.", "Descriptive only; not an entry filter or evidence of edge."]}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db", required=True, type=Path); p.add_argument("--symbol", default="XAUUSD_l"); p.add_argument("--output", required=True, type=Path)
    a = p.parse_args(); result = audit(a.db, a.symbol)
    a.output.parent.mkdir(parents=True, exist_ok=True); a.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__": main()
