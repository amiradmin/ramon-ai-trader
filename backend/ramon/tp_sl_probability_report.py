"""Read-only realized TP-before-SL baseline, segmented by decision horizon.

This is deliberately a report, not a live model: it uses only broker outcomes
whose terminal reason is TP or SL and refuses to convert censored/manual exits
into labels.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from collections import defaultdict
from pathlib import Path


def summarize(db: str | Path, *, symbol: str = "XAUUSD_l", horizons: tuple[int, ...] = (5, 15, 30), minimum_samples: int = 30) -> dict[str, object]:
    if not horizons or any(value <= 0 for value in horizons) or minimum_samples < 1:
        raise ValueError("invalid horizons or minimum_samples")
    uri = Path(db).expanduser().resolve().as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as con:
        rows = con.execute(
            """SELECT direction,opened,closed,exit_reason FROM trade_outcomes
               WHERE symbol=? AND exit_reason IN ('DEAL_REASON_TP','DEAL_REASON_SL')
               ORDER BY opened""", (symbol,)
        ).fetchall()
    groups: dict[tuple[int, str], list[str]] = defaultdict(list)
    for direction, opened, closed, reason in rows:
        elapsed = max(0, int(closed) - int(opened))
        for minutes in horizons:
            if elapsed <= minutes * 60:
                groups[(minutes, str(direction))].append(str(reason))
    cells = []
    for (minutes, direction), values in sorted(groups.items()):
        tp = sum(value == "DEAL_REASON_TP" for value in values)
        cells.append({
            "horizon_minutes": minutes, "direction": direction, "samples": len(values),
            "tp_before_sl_probability": round(tp / len(values), 6),
            "eligible_for_shadow": int(len(values) >= minimum_samples),
        })
    return {
        "schema_version": 1, "mode": "READ_ONLY_SHADOW", "symbol": symbol,
        "minimum_samples": minimum_samples, "terminal_tp_or_sl_outcomes": len(rows),
        "cells": cells,
        "limitations": "broker terminal TP/SL labels only; no probability is emitted for censored/manual exits",
        "live_use": "prohibited_until-calibrated-walk-forward-model",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True)
    parser.add_argument("--symbol", default="XAUUSD_l")
    parser.add_argument("--minimum-samples", type=int, default=30)
    args = parser.parse_args()
    print(json.dumps(summarize(args.db, symbol=args.symbol, minimum_samples=args.minimum_samples), ensure_ascii=False))


if __name__ == "__main__":
    main()
