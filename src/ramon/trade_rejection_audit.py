"""Read-only diagnostics for exclusions in the executed-trade research dataset.

Mirrors trade-first builder eligibility, with granular rejection reasons and
missing candle open timestamps. Does not recover, relabel or rewrite trades.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from .executed_trade_dataset_builder import load_inputs, _valid_offset
from .multitimeframe_dataset_builder import STEPS, _closed_features

DETAIL_FIELDS = [
    "trade_key", "sample_key", "reason", "all_reasons", "captured_utc",
    "signal_bar_raw", "signal_bar_utc", "opened_utc", "closed_utc",
    "direction_trade", "direction_decision", "time_delta_open_minus_capture_seconds",
    "missing_timeframe", "missing_candle_open_utc", "missing_candle_open_iso",
]


def inspect_trades(decisions, trades, bars, *, broker_utc_offset_seconds):
    offset = _valid_offset(broker_utc_offset_seconds)
    by_key = defaultdict(list)
    for d in decisions:
        if d.get("sample_key") and d.get("symbol"):
            by_key[(d["symbol"], d["sample_key"])].append(d)
    details = []
    reasons = Counter()
    all_reason_counts = Counter()
    missing_stamps = defaultdict(Counter)
    for trade in sorted(trades, key=lambda x: (str(x.get("opened")), str(x.get("trade_key")))):
        item = {key: "" for key in DETAIL_FIELDS}
        item["trade_key"] = trade.get("trade_key") or ""
        item["sample_key"] = trade.get("sample_key") or ""
        item["direction_trade"] = trade.get("direction") or ""
        issues = []
        linked = by_key.get((trade.get("symbol"), trade.get("sample_key")), [])
        if not linked:
            issues.append("UNMATCHED_DECISION")
        elif len(linked) > 1:
            issues.append("AMBIGUOUS_DECISION")
        else:
            d = linked[0]
            item["direction_decision"] = d.get("direction") or ""
            item["signal_bar_raw"] = d.get("signal_bar_time") or ""
            item["captured_utc"] = d.get("captured") or ""
            item["opened_utc"] = trade.get("opened") or ""
            item["closed_utc"] = trade.get("closed") or ""
            try:
                captured = int(d["captured"])
                opened = int(trade["opened"])
                closed = int(trade["closed"])
                raw_bar = int(d["signal_bar_time"])
                utc_bar = raw_bar - offset
                item["signal_bar_utc"] = utc_bar
                item["time_delta_open_minus_capture_seconds"] = opened - captured
                if utc_bar <= 0:
                    issues.append("INVALID_SIGNAL_BAR_TIME")
                if captured < utc_bar:
                    issues.append("SIGNAL_BAR_AFTER_CAPTURE")
                if opened < captured:
                    issues.append("TRADE_OPEN_BEFORE_DECISION")
                if closed < opened:
                    issues.append("TRADE_CLOSE_BEFORE_OPEN")
                if opened <= 0 or closed <= 0:
                    issues.append("NONPOSITIVE_TRADE_TIME")
                if d.get("direction") != trade.get("direction"):
                    issues.append("DIRECTION_MISMATCH")
                if not all(math.isfinite(float(trade[k])) for k in ("net_r", "net_units")):
                    issues.append("INVALID_OUTCOME_VALUES")
                # Only classify candle coverage if causality is established.
                if not issues:
                    for tf, step in STEPS.items():
                        if _closed_features(bars[tf], captured, step) is None:
                            last = (captured // step) * step - step
                            for stamp in (last - step, last):
                                if stamp not in bars[tf]:
                                    missing_stamps[tf][stamp] += 1
                                    if not item["missing_timeframe"]:
                                        item["missing_timeframe"] = tf
                                        item["missing_candle_open_utc"] = stamp
                                        item["missing_candle_open_iso"] = datetime.fromtimestamp(
                                            stamp, timezone.utc).isoformat()
                            issues.append(f"{tf}_MISSING_OR_INVALID_CONTEXT")
                            break
            except (TypeError, ValueError, OverflowError):
                issues.append("MALFORMED_TIMESTAMP_OR_OUTCOME")
        if not issues:
            issues.append("COMPLETE_CONTEXT")
        item["reason"] = issues[0]
        item["all_reasons"] = ";".join(issues)
        details.append(item)
        reasons[issues[0]] += 1
        all_reason_counts.update(issues)
    report = {
        "mode": "OFFLINE_TRADE_REJECTION_DIAGNOSTICS_ONLY",
        "trades_scanned": len(trades),
        "first_rejection_counts": dict(sorted(reasons.items())),
        "all_reason_counts": dict(sorted(all_reason_counts.items())),
        "missing_candle_open_timestamps": {
            tf: [{"open_utc": stamp,
                  "open_iso": datetime.fromtimestamp(stamp, timezone.utc).isoformat(),
                  "affected_trades": count}
                 for stamp, count in sorted(stamps.items())]
            for tf, stamps in missing_stamps.items()
        },
        "broker_utc_offset_seconds": offset,
        "warning": "Diagnostics, not evidence a rejected trade can safely be recovered.",
    }
    return details, report


def export_rejection_audit(db: Path, h1_csv: Path, *, symbol: str,
                           broker_utc_offset_seconds: int,
                           output: Path, report: Path):
    if output.resolve() == report.resolve():
        raise ValueError("output and report must differ")
    decisions, trades, bars = load_inputs(db, h1_csv, symbol=symbol)
    details, summary = inspect_trades(
        decisions, trades, bars,
        broker_utc_offset_seconds=broker_utc_offset_seconds)
    summary["symbol"] = symbol
    summary["decisions_scanned"] = len(decisions)
    summary["missing_candle_counts"] = {
        tf: len(entries) for tf, entries in
        summary["missing_candle_open_timestamps"].items()}
    output.parent.mkdir(parents=True, exist_ok=True)
    report.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=DETAIL_FIELDS)
        writer.writeheader()
        writer.writerows(details)
    report.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
                      encoding="utf-8")
    return summary


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db", required=True, type=Path)
    p.add_argument("--h1-csv", required=True, type=Path)
    p.add_argument("--symbol", default="XAUUSD_l")
    p.add_argument("--broker-utc-offset-seconds", required=True, type=int)
    p.add_argument("--output", required=True, type=Path)
    p.add_argument("--report", required=True, type=Path)
    a = p.parse_args()
    result = export_rejection_audit(
        a.db, a.h1_csv, symbol=a.symbol,
        broker_utc_offset_seconds=a.broker_utc_offset_seconds,
        output=a.output, report=a.report)
    printable = {k: v for k, v in result.items()
                 if k != "missing_candle_open_timestamps"}
    print(json.dumps(printable, ensure_ascii=False))


if __name__ == "__main__":
    main()
