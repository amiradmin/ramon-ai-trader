"""Read-only audit of direction provenance for exact linked executed trades.

Observes stored fields only. Matching a stored final_decision is evidence
of agreement with that field, NOT broker-confirmed execution provenance.
Never relabels trades or changes eligibility in the dataset builder.
"""
from __future__ import annotations

import argparse
import csv
import json
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

from .executed_trade_dataset_builder import _valid_offset

FIELDS = [
    "trade_key", "sample_key", "trade_direction", "decision_direction",
    "base_decision", "final_decision", "captured_utc", "opened_utc",
    "signal_bar_raw", "signal_bar_utc", "time_order_ok",
    "classification", "final_agrees_with_trade",
]

DIRECTIONS = {"BUY", "SELL"}


def _norm(value):
    return str(value or "").strip().upper()


def classify(trade, decision, *, offset):
    t_dir = _norm(trade.get("direction"))
    d_dir = _norm(decision.get("direction"))
    base = _norm(decision.get("base_decision"))
    final = _norm(decision.get("final_decision"))
    item = dict(
        trade_key=str(trade.get("trade_key") or ""),
        sample_key=str(trade.get("sample_key") or ""),
        trade_direction=t_dir, decision_direction=d_dir,
        base_decision=base, final_decision=final,
        captured_utc=decision.get("captured") or "",
        opened_utc=trade.get("opened") or "",
        signal_bar_raw=decision.get("signal_bar_time") or "",
        signal_bar_utc="", time_order_ok=False, classification="",
        final_agrees_with_trade=False,
    )
    try:
        captured = int(decision["captured"])
        opened = int(trade["opened"])
        raw_bar = int(decision["signal_bar_time"])
        utc_bar = raw_bar - offset
        item["signal_bar_utc"] = utc_bar
        item["time_order_ok"] = 0 < utc_bar <= captured <= opened
    except (TypeError, ValueError, OverflowError):
        item["classification"] = "MALFORMED_TIME"
        return item
    if not item["time_order_ok"]:
        item["classification"] = "NONCAUSAL_TIME"
    elif t_dir not in DIRECTIONS:
        item["classification"] = "INVALID_TRADE_DIRECTION"
    elif d_dir == t_dir:
        item["classification"] = "RECORDED_DIRECTION_MATCH"
    elif final in DIRECTIONS and final == t_dir:
        item["classification"] = "FINAL_FIELD_MATCHES_TRADE"
        item["final_agrees_with_trade"] = True
    elif base in DIRECTIONS and base == t_dir:
        item["classification"] = "BASE_FIELD_MATCHES_TRADE_ONLY"
    elif not final:
        item["classification"] = "FINAL_FIELD_MISSING"
    elif final == "WAIT":
        item["classification"] = "FINAL_FIELD_WAIT"
    elif final not in DIRECTIONS:
        item["classification"] = "FINAL_FIELD_OTHER"
    else:
        item["classification"] = "NO_RECORDED_DIRECTION_MATCH"
    return item


def inspect_direction_provenance(decisions, trades, *, broker_utc_offset_seconds):
    offset = _valid_offset(broker_utc_offset_seconds)
    decisions_by_key = defaultdict(list)
    for d in decisions:
        if d.get("sample_key") and d.get("symbol"):
            decisions_by_key[(d["symbol"], d["sample_key"])].append(d)
    all_counts = Counter()
    mismatch_counts = Counter()
    details = []
    for t in sorted(trades, key=lambda r: (str(r.get("opened")), str(r.get("trade_key")))):
        linked = decisions_by_key.get((t.get("symbol"), t.get("sample_key")), [])
        if len(linked) != 1:
            result = {k: "" for k in FIELDS}
            result.update(trade_key=str(t.get("trade_key") or ""),
                          sample_key=str(t.get("sample_key") or ""),
                          trade_direction=_norm(t.get("direction")),
                          classification="UNMATCHED_DECISION" if not linked else "AMBIGUOUS_DECISION")
        else:
            result = classify(t, linked[0], offset=offset)
        details.append(result)
        code = result["classification"]
        all_counts[code] += 1
        if (len(linked) == 1
            and _norm(linked[0].get("direction")) != _norm(t.get("direction"))):
            mismatch_counts[code] += 1
    return details, {
        "mode": "OFFLINE_DIRECTION_PROVENANCE_ONLY",
        "trades_scanned": len(trades),
        "broker_utc_offset_seconds": offset,
        "classification_counts": dict(sorted(all_counts.items())),
        "mismatched_direction_counts": dict(sorted(mismatch_counts.items())),
        "direction_mismatches_total": sum(mismatch_counts.values()),
        "warning": ("A matching base/final decision field alone does not prove "
                    "the EA intentionally reversed a trade or that an entry was correct. "
                    "No dataset eligibility is changed."),
    }


def load_direction_inputs(db: Path, *, symbol: str):
    with sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True) as con:
        con.row_factory = sqlite3.Row
        # The persisted history contract includes final_decision as a nullable
        # column added by schema migration; fail explicitly if unavailable.
        columns = {r[1] for r in con.execute("PRAGMA table_info(decision_samples)")}
        required = {"sample_key", "symbol", "captured", "signal_bar_time",
                    "direction", "base_decision", "final_decision"}
        if not required.issubset(columns):
            raise ValueError(f"missing decision provenance columns: {sorted(required - columns)}")
        decisions = [dict(row) for row in con.execute(
            "SELECT sample_key,symbol,captured,signal_bar_time,direction,"
            "base_decision,final_decision FROM decision_samples WHERE symbol=?",
            (symbol,),
        )]
        trades = [dict(row) for row in con.execute(
            "SELECT trade_key,sample_key,symbol,opened,direction "
            "FROM trade_outcomes WHERE symbol=?", (symbol,),
        )]
    return decisions, trades


def export_direction_audit(db: Path, *, symbol: str,
                           broker_utc_offset_seconds: int,
                           output: Path, report: Path):
    if output.resolve() == report.resolve():
        raise ValueError("CSV and JSON report paths must differ")
    decisions, trades = load_direction_inputs(db, symbol=symbol)
    rows, summary = inspect_direction_provenance(
        decisions, trades, broker_utc_offset_seconds=broker_utc_offset_seconds)
    summary["symbol"] = symbol
    summary["decisions_scanned"] = len(decisions)
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
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db", required=True, type=Path)
    p.add_argument("--symbol", default="XAUUSD_l")
    p.add_argument("--broker-utc-offset-seconds", required=True, type=int)
    p.add_argument("--output", required=True, type=Path)
    p.add_argument("--report", required=True, type=Path)
    a = p.parse_args()
    summary = export_direction_audit(
        a.db, symbol=a.symbol,
        broker_utc_offset_seconds=a.broker_utc_offset_seconds,
        output=a.output, report=a.report)
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
