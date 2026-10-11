"""Offline Ramon position lifecycle audit from research_events.

Observed SL transitions are not proof of successful broker modification;
this report never changes orders or the live decision service.
"""
from __future__ import annotations

import argparse
import json
import math
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path


def _number(value):
    try:
        v = float(value)
        return v if math.isfinite(v) else None
    except (ValueError, TypeError):
        return None


def analyze_events(rows: list[dict]) -> dict:
    events = defaultdict(list)
    kinds = Counter()
    gaps = 0
    malformed = 0
    for row in rows:
        kind = str(row["kind"])
        kinds[kind] += 1
        if kind == "telemetry_gap":
            gaps += 1
        position = str(row.get("position_id") or "").strip()
        if not position:
            continue
        try:
            payload = json.loads(row.get("body_json") or "{}")
            data = payload.get("data", {})
            if not isinstance(data, dict):
                raise ValueError("invalid data")
        except (TypeError, ValueError):
            malformed += 1
            continue
        # Position IDs are account-scoped, not globally unique.
        key = (str(row.get("account_key") or ""), position)
        events[key].append((float(row["received_utc"]), str(row["event_id"]),
                            kind, data, row.get("sample_key")))
    positions = []
    for (account, position), history in sorted(events.items()):
        history.sort(key=lambda e: (e[0], e[1]))
        baseline = None
        sl_changes = []
        stage_changes = []
        last_stage = None
        observed_quotes = 0
        for when, event_id, kind, data, sample_key in history:
            # 'position_change' is a snapshot event; compare to preceding
            # observed snapshot only, not an assumed order modification.
            if kind == "position_quote":
                observed_quotes += 1
            if kind not in ("position_quote", "position_change"):
                continue
            sl = _number(data.get("sl"))
            if sl is not None:
                if baseline is not None and abs(sl - baseline) > 1e-8:
                    sl_changes.append({"observed_utc": when, "from": baseline, "to": sl,
                                       "source_event_kind": kind})
                baseline = sl
            stage = data.get("tp_stage")
            if stage is not None and stage != last_stage:
                if last_stage is not None:
                    stage_changes.append({"observed_utc": when, "from": last_stage, "to": stage})
                last_stage = stage
        positions.append({
            "position_id": position,
            "account_key": account,
            "events": len(history),
            "position_quote_events": observed_quotes,
            "observed_sl_transitions": sl_changes,
            "observed_tp_stage_transitions": stage_changes,
            "has_position_change": any(e[2] == "position_change" for e in history),
        })
    return {
        "mode": "READ_ONLY_LIFECYCLE_AUDIT",
        "event_kinds": dict(sorted(kinds.items())),
        "telemetry_gap_events": gaps,
        "malformed_position_payloads": malformed,
        "positions_with_events": len(positions),
        "positions": positions,
        "limitations": [
            "Observed SL transitions are snapshots, not broker-confirmed SL modify events.",
            "Position snapshots can miss intermediate changes and are sensitive to telemetry gaps.",
            "received_utc can be batch reception time: do not use as proven broker event chronology.",
            "No automatic causal attribution of profit protection or position exits.",
        ],
    }


def audit_database(db: Path, symbol: str = "XAUUSD_l") -> dict:
    if not symbol:
        raise ValueError("symbol required")
    with sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        rows = [dict(row) for row in conn.execute(
            """SELECT event_id, account_key, position_id, sample_key, kind,
                      received_utc, broker_time_msc, body_json
               FROM research_events WHERE symbol=?
               ORDER BY received_utc, event_id""", (symbol,)
        )]
    return analyze_events(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--symbol", default="XAUUSD_l")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = audit_database(args.db, symbol=args.symbol)
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered)


if __name__ == "__main__":
    main()
