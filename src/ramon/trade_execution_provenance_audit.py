"""Read-only source-of-execution audit for recorded direction conflicts.

Never asserts WAIT overrides are legitimate, nor that entry_source proves
specific EA authorization. Existing DB fields only; optional research event
counts are corroborating telemetry, not broker-confirmed order provenance.
"""
from __future__ import annotations

import argparse
import csv
import json
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

from .executed_trade_dataset_builder import _valid_offset
from .trade_direction_provenance_audit import classify, _norm

FIELDS = [
    "trade_key", "sample_key", "classification", "execution_provenance",
    "trade_direction", "decision_direction", "base_decision", "final_decision",
    "entry_source", "trade_role", "entry_magic", "entry_ea_version",
    "captured_utc", "opened_utc", "signal_bar_utc", "time_order_ok",
    "event_count_same_sample", "event_kinds_same_sample",
    "evidence_limitations",
]


def audit_execution(decisions, trades, events=(), *, broker_utc_offset_seconds):
    offset = _valid_offset(broker_utc_offset_seconds)
    decisions_by_key = defaultdict(list)
    for d in decisions:
        if d.get("sample_key") and d.get("symbol"):
            decisions_by_key[(d["symbol"], d["sample_key"])].append(d)
    events_by_key = defaultdict(Counter)
    for e in events:
        # Counts are only by exact (symbol,sample_key), never closest time.
        if e.get("symbol") and e.get("sample_key"):
            events_by_key[(e["symbol"], e["sample_key"])][_norm(e.get("kind"))] += 1
    rows = []
    counts = Counter()
    targets = Counter()
    for t in sorted(trades, key=lambda r: (str(r.get("opened")), str(r.get("trade_key")))):
        matched = decisions_by_key.get((t.get("symbol"), t.get("sample_key")), [])
        if len(matched) == 1:
            evidence = classify(t, matched[0], offset=offset)
            classification = evidence["classification"]
        else:
            evidence = {key: "" for key in (
                "trade_direction", "decision_direction", "base_decision",
                "final_decision", "captured_utc", "opened_utc",
                "signal_bar_utc", "time_order_ok")}
            evidence["trade_direction"] = _norm(t.get("direction"))
            classification = "UNMATCHED_DECISION" if not matched else "AMBIGUOUS_DECISION"
        raw_events = events_by_key.get((t.get("symbol"), t.get("sample_key")), Counter())
        if classification in {"FINAL_FIELD_WAIT", "NO_RECORDED_DIRECTION_MATCH"}:
            provenance = "UNRESOLVED_EXECUTION_WITH_CONFLICT"
        elif classification == "FINAL_FIELD_MATCHES_TRADE":
            provenance = "STORED_FINAL_AGREEMENT_EXECUTION_UNVERIFIED"
        elif classification == "RECORDED_DIRECTION_MATCH":
            provenance = "STORED_DIRECTION_AGREEMENT_EXECUTION_UNVERIFIED"
        else:
            provenance = "UNRESOLVED_IDENTITY_OR_CAUSALITY"
        row = {
            "trade_key": t.get("trade_key") or "",
            "sample_key": t.get("sample_key") or "",
            "classification": classification,
            "execution_provenance": provenance,
            "trade_direction": evidence["trade_direction"],
            "decision_direction": evidence["decision_direction"],
            "base_decision": evidence["base_decision"],
            "final_decision": evidence["final_decision"],
            "entry_source": t.get("entry_source") or "",
            "trade_role": t.get("trade_role") or "",
            "entry_magic": t.get("entry_magic") if t.get("entry_magic") is not None else "",
            "entry_ea_version": t.get("entry_ea_version") or "",
            "captured_utc": evidence["captured_utc"],
            "opened_utc": evidence["opened_utc"],
            "signal_bar_utc": evidence["signal_bar_utc"],
            "time_order_ok": evidence["time_order_ok"],
            "event_count_same_sample": sum(raw_events.values()),
            "event_kinds_same_sample": ";".join(
                f"{kind}:{count}" for kind, count in sorted(raw_events.items())),
            "evidence_limitations": (
                "Stored source/role/magic and sample-key events are observational; "
                "no entry authorization or exact MT5 order routing is established."),
        }
        rows.append(row)
        counts[classification] += 1
        if classification in {"FINAL_FIELD_WAIT", "NO_RECORDED_DIRECTION_MATCH"}:
            targets[(classification,
                     str(row["entry_source"] or "MISSING"),
                     str(row["trade_role"] or "MISSING"),
                     str(row["entry_magic"] or "MISSING"))] += 1
    return rows, {
        "mode": "OFFLINE_EXECUTION_PROVENANCE_AUDIT_ONLY",
        "trades_scanned": len(trades),
        "broker_utc_offset_seconds": offset,
        "classification_counts": dict(sorted(counts.items())),
        "unresolved_conflicts": sum(
            counts[x] for x in ("FINAL_FIELD_WAIT", "NO_RECORDED_DIRECTION_MATCH")),
        "conflict_entry_provenance_groups": [
            {"classification": k[0], "entry_source": k[1], "trade_role": k[2],
             "entry_magic": k[3], "trades": n}
            for k, n in sorted(targets.items(),
                               key=lambda kv: (-kv[1], kv[0]))
        ],
        "limitations": [
            "A persisted WAIT and a later trade do not prove an EA violation.",
            "A recorded entry_source, trade_role or magic is not authorization evidence.",
            "Research events linked by sample_key are counts only, not confirmed entry instructions.",
            "An exact source-of-execution attribution requires decision/request/order/deal identifiers.",
            "No live execution or research label eligibility is changed.",
        ],
    }


def load_inputs(db: Path, symbol: str):
    with sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True) as con:
        con.row_factory = sqlite3.Row
        decisions = [dict(x) for x in con.execute(
            "SELECT sample_key,symbol,signal_bar_time,captured,direction,"
            "base_decision,final_decision FROM decision_samples WHERE symbol=?",
            (symbol,),
        )]
        cols = {x[1] for x in con.execute("PRAGMA table_info(trade_outcomes)")}
        optional = ("entry_source", "trade_role", "entry_magic", "entry_ea_version")
        select_optional = ",".join(
            name if name in cols else f"NULL AS {name}" for name in optional)
        trades = [dict(x) for x in con.execute(
            "SELECT trade_key,sample_key,symbol,opened,direction,"
            f"{select_optional} FROM trade_outcomes WHERE symbol=?",
            (symbol,),
        )]
        tables = {x[0] for x in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        events = []
        if "research_events" in tables:
            event_cols = {x[1] for x in con.execute("PRAGMA table_info(research_events)")}
            if {"sample_key", "symbol", "kind"}.issubset(event_cols):
                events = [dict(x) for x in con.execute(
                    "SELECT symbol,sample_key,kind FROM research_events "
                    "WHERE symbol=? AND sample_key IS NOT NULL", (symbol,),
                )]
    return decisions, trades, events


def export_execution_audit(db: Path, *, symbol: str,
                           broker_utc_offset_seconds: int,
                           output: Path, report: Path):
    if output.resolve() == report.resolve():
        raise ValueError("CSV and JSON paths must differ")
    decisions, trades, events = load_inputs(db, symbol)
    rows, summary = audit_execution(
        decisions, trades, events,
        broker_utc_offset_seconds=broker_utc_offset_seconds)
    summary["symbol"] = symbol
    summary["decisions_scanned"] = len(decisions)
    summary["research_events_scanned"] = len(events)
    output.parent.mkdir(parents=True, exist_ok=True)
    report.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    report.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n",
                      encoding="utf-8")
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--symbol", default="XAUUSD_l")
    parser.add_argument("--broker-utc-offset-seconds", required=True, type=int)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(export_execution_audit(
        args.db, symbol=args.symbol,
        broker_utc_offset_seconds=args.broker_utc_offset_seconds,
        output=args.output, report=args.report), ensure_ascii=False))


if __name__ == "__main__":
    main()
