"""Read-only UTC daily report of recorded Ramon model decisions.

Request count, unique decision keys and distinct signal-bar opportunities are
separate metrics. None of them is an EA order-attempt count.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3


def _dict(value):
    try:
        result = json.loads(value) if isinstance(value, str) else value
        return result if isinstance(result, dict) else {}
    except (ValueError, TypeError):
        return {}


def _utc_day(day: str) -> int:
    return int(datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())


def build_report(db: str | Path, *, day: str, symbol: str = "XAUUSD_l") -> dict:
    start = _utc_day(day)
    end = start + 86400
    path = Path(db).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as con:
        con.row_factory = sqlite3.Row
        cols = {r[1] for r in con.execute("PRAGMA table_info(decision_samples)")}
        required = {"captured", "symbol", "signal_bar_time", "final_decision", "base_decision", "model_metadata"}
        if not required <= cols:
            raise ValueError("decision_samples schema lacks required columns")
        key_col = "sample_key" if "sample_key" in cols else "NULL AS sample_key"
        rows = con.execute(
            f"""SELECT captured,signal_bar_time,final_decision,base_decision,
                       model_metadata,{key_col}
                FROM decision_samples
                WHERE symbol=? AND captured>=? AND captured<?
                ORDER BY captured,id""",
            (symbol, start, end),
        ).fetchall()

    directions = Counter()
    base_directions = Counter()
    reasons = Counter()
    request_ids = set()
    opportunities = defaultdict(lambda: {"decisions": set(), "reasons": set(), "samples": 0})
    unkeyed = 0
    unknown_reasons = 0
    for row in rows:
        metadata = _dict(row["model_metadata"])
        audit = _dict(metadata.get("decision_audit"))
        final = _dict(audit.get("final"))
        base = _dict(audit.get("base"))
        decision = str(final.get("decision") or row["final_decision"] or "UNKNOWN").upper()
        base_decision = str(base.get("decision") or row["base_decision"] or "UNKNOWN").upper()
        reason = str(final.get("reason") or "UNKNOWN")
        if reason == "UNKNOWN":
            unknown_reasons += 1
        if decision not in {"BUY", "SELL", "WAIT"}:
            decision = "UNKNOWN"
        if base_decision not in {"BUY", "SELL", "WAIT"}:
            base_decision = "UNKNOWN"
        directions[decision] += 1
        base_directions[base_decision] += 1
        if decision == "WAIT":
            reasons[reason] += 1
        sample_key = str(row["sample_key"] or "")
        # Stable legacy fallback: captured is uniquely indexed per symbol.
        identity = sample_key if sample_key else f"legacy:{row['captured']}"
        if not sample_key:
            unkeyed += 1
        request_ids.add(identity)
        # Distinct candle+side buckets are *opportunities*, not orders.
        # Repeated 5-second evaluations of a candle count once per side.
        candidate = str(metadata.get("direction") or base.get("direction") or "").upper()
        if candidate not in {"BUY", "SELL"}:
            candidate = decision if decision in {"BUY", "SELL"} else "UNKNOWN"
        group = opportunities[(row["signal_bar_time"], candidate)]
        group["decisions"].add(decision)
        group["reasons"].add(reason)
        group["samples"] += 1

    wait_groups = [
        (bar, side, group) for (bar, side), group in opportunities.items()
        if "WAIT" in group["decisions"]
    ]
    return {
        "day_utc": day,
        "symbol": symbol,
        "recorded_requests": len(rows),
        "unique_saved_decision_ids": len(request_ids),
        "missing_sample_key_count": unkeyed,
        "final_decisions_by_request": dict(sorted(directions.items())),
        "base_decisions_by_request": dict(sorted(base_directions.items())),
        "wait_reasons_by_request": dict(sorted(reasons.items(), key=lambda x: (-x[1], x[0]))),
        "unknown_final_reason_count": unknown_reasons,
        "signal_bar_direction_opportunities": len(opportunities),
        "opportunities_with_any_wait": len(wait_groups),
        "wait_reasons_by_opportunity": dict(sorted(Counter(
            reason for _, _, group in wait_groups for reason in group["reasons"] if reason != "UNKNOWN"
        ).items(), key=lambda x: (-x[1], x[0]))),
        "notes": [
            "All grouping and day boundaries use saved model samples and UTC.",
            "Request counts and unique sample IDs are not independent trading opportunities.",
            "Opportunity groups combine all snapshots for one M15 signal bar and candidate side.",
            "A group may have multiple WAIT reasons; reason totals can exceed WAIT opportunity count.",
            "UNKNOWN direction/reason means the historical sample does not contain enough evidence.",
            "EA execution vetoes, manual trades and order attempts cannot be inferred from model decision samples.",
        ],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="data/ramon_history.sqlite3")
    parser.add_argument("--symbol", default="XAUUSD_l")
    parser.add_argument("--day", default=(datetime.now(timezone.utc) - timedelta(days=0)).strftime("%Y-%m-%d"))
    parser.add_argument("--output", help="optional JSON output path")
    args = parser.parse_args(argv)
    report = build_report(args.db, day=args.day, symbol=args.symbol)
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
