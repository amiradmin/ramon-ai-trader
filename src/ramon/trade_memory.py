"""Read-only trade-memory summaries for shadow research and dashboards."""
from __future__ import annotations

import argparse
import json
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path


def summarize(db: str | Path, *, symbol: str = "XAUUSD_l", minimum_samples: int = 20) -> dict[str, object]:
    """Group learnable broker outcomes by UTC hour/direction; never write the DB."""
    if minimum_samples < 1:
        raise ValueError("minimum_samples must be positive")
    uri = Path(db).expanduser().resolve().as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as con:
        rows = con.execute(
            """SELECT direction,opened,net_r FROM trade_outcomes
               WHERE symbol=? AND training_status='LEARNABLE' ORDER BY opened""", (symbol,)
        ).fetchall()
    cells: dict[tuple[int, str], list[float]] = defaultdict(list)
    for direction, opened, net_r in rows:
        hour = datetime.fromtimestamp(int(opened), tz=timezone.utc).hour
        cells[(hour, str(direction))].append(float(net_r))
    result = []
    for (hour, direction), values in sorted(cells.items()):
        wins = sum(value > 0 for value in values)
        result.append({
            "utc_hour": hour, "direction": direction, "samples": len(values),
            "mean_net_r": round(sum(values) / len(values), 6),
            "win_rate": round(wins / len(values), 6),
            "eligible_for_shadow": int(len(values) >= minimum_samples),
        })
    return {"schema_version": 1, "mode": "READ_ONLY_SHADOW", "symbol": symbol,
            "minimum_samples": minimum_samples, "learnable_outcomes": len(rows), "cells": result,
            "live_use": "prohibited_until_walk_forward_validation"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True)
    parser.add_argument("--symbol", default="XAUUSD_l")
    parser.add_argument("--minimum-samples", type=int, default=20)
    args = parser.parse_args()
    print(json.dumps(summarize(args.db, symbol=args.symbol, minimum_samples=args.minimum_samples), ensure_ascii=False))


if __name__ == "__main__":
    main()
