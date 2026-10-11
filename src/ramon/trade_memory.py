"""Read-only, point-in-time Trade Memory diagnostics for Ramon.

Uses only CLOSED outcomes known before the requested UTC cutoff. Produces
descriptive historical evidence; never authorizes orders or learns online.
"""
from __future__ import annotations

import argparse
import json
import math
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from .trade_audit import summarize


def _utc_epoch(value: str) -> int:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("as-of timestamp must include timezone")
    return int(dt.timestamp())


def memory_snapshot(db: str | Path, *, as_of: int, symbol: str = "XAUUSD_l",
                    minimum_samples: int = 30) -> dict:
    """Summarize only fully closed, uniquely attributed observations.

    A sample may be joined to its original decision only through sample_key.
    Never fill missing regime labels with predictions from later timestamps.
    """
    if as_of <= 0 or minimum_samples < 1 or not symbol:
        raise ValueError("invalid snapshot parameters")
    path = Path(db).expanduser().resolve(strict=True)
    # SQLite immutable=1 is unsafe for DBs receiving WAL updates: use mode=ro
    # and allow SQLite to read WAL snapshots without writing to application data.
    uri = "file:" + quote(str(path), safe="/") + "?mode=ro"
    with sqlite3.connect(uri, uri=True, timeout=10) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT t.trade_key, t.sample_key, t.opened, t.closed, t.net_r,
                   t.net_units, t.direction, t.training_status,
                   d.captured, d.final_decision, d.base_decision,
                   d.bundle_id, d.entry_ea_version
            FROM trade_outcomes AS t
            INNER JOIN decision_samples AS d ON d.sample_key = t.sample_key
            WHERE t.symbol = ? AND d.symbol = ?
              AND t.opened <= ? AND t.closed <= ?
              AND t.closed >= t.opened
              AND d.captured <= t.opened AND d.captured <= ?
            ORDER BY t.closed, t.trade_key
        """, (symbol, symbol, as_of, as_of, as_of)).fetchall()
    usable = []
    discarded = 0
    for row in rows:
        net_r = row["net_r"]
        net_units = row["net_units"]
        if (net_r is None or net_units is None or
                not math.isfinite(float(net_r)) or
                not math.isfinite(float(net_units))):
            discarded += 1
            continue
        usable.append(row)

    by_direction: dict[str, list[float]] = {}
    by_version: dict[str, list[float]] = {}
    by_hour: dict[str, list[float]] = {}
    for row in usable:
        value = float(row["net_r"])
        direction = row["direction"] if row["direction"] in ("BUY", "SELL") else "UNKNOWN"
        by_direction.setdefault(direction, []).append(value)
        by_version.setdefault(str(row["entry_ea_version"] or "UNKNOWN"), []).append(value)
        hour = datetime.fromtimestamp(row["opened"], tz=timezone.utc).strftime("%H")
        by_hour.setdefault(hour, []).append(value)

    def groups(source: dict[str, list[float]]) -> dict:
        return {
            name: {"eligible_for_review": len(values) >= minimum_samples,
                   **summarize(values)}
            for name, values in sorted(source.items())
        }

    return {
        "mode": "SHADOW_READ_ONLY",
        "as_of_utc": datetime.fromtimestamp(as_of, tz=timezone.utc).isoformat(),
        "symbol": symbol,
        "minimum_samples": minimum_samples,
        "matched_closed_trades": len(usable),
        "discarded_nonfinite": discarded,
        "overall_net_r": summarize([float(row["net_r"]) for row in usable]),
        "by_direction": groups(by_direction),
        "by_entry_version": groups(by_version),
        "by_entry_hour_utc": groups(by_hour),
        "limitations": [
            "Descriptive in-sample results only: no directional trading recommendation.",
            "Unknown/unmatched trade outcomes are excluded; report coverage separately.",
            "No regime assignment without trustworthy entry-time regime provenance.",
            "Risk-normalized R is not account-currency profit.",
            "Require purged walk-forward out-of-sample evaluation before deployment.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--as-of", required=True, help="ISO-8601 timestamp with UTC offset")
    parser.add_argument("--symbol", default="XAUUSD_l")
    parser.add_argument("--minimum-samples", type=int, default=30)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = memory_snapshot(args.db, as_of=_utc_epoch(args.as_of),
                             symbol=args.symbol, minimum_samples=args.minimum_samples)
    report = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(report, encoding="utf-8")
    else:
        print(report)


if __name__ == "__main__":
    main()
