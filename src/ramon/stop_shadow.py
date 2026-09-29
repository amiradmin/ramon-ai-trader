"""Read-only chronological test of a pre-entry full-stop-loss classifier.

Uses completed M5 candles and recorded Ramon trade outcomes. The input does
not contain actual entry-time Chronos features, so this is not the live role.
"""

from __future__ import annotations

import argparse
from bisect import bisect_right
import json
from math import isfinite
from pathlib import Path

from .addon_research import read_m5, read_trades
from .ensemble import train_binary_logistic
from .loss_audit import parse_report


FEATURES = ("side_sell", "spread_atr", "atr_pct", "ret_4_atr", "ret_12_atr",
            "range_12_atr")


def examples(m5, trades, outcomes):
    times = [bar.time for bar in m5]
    rows = []
    missing = {}
    for trade in trades:
        outcome = outcomes.get(trade.number)
        if outcome is None:
            missing["outcome_missing"] = missing.get("outcome_missing", 0) + 1
            continue
        # The report's entry minute can precede the actual fill by 59 seconds.
        # Stop at the last completed M5 bar before the recorded minute.
        index = bisect_right(times, trade.opened_broker - 300) - 1
        if (index < 15 or index >= len(m5)
                or trade.opened_broker - (m5[index].time + 300) >= 300):
            missing["history_missing"] = missing.get("history_missing", 0) + 1
            continue
        window = m5[index - 15:index + 1]
        if any(right.time - left.time != 300 for left, right in zip(window, window[1:])):
            missing["bar_gap"] = missing.get("bar_gap", 0) + 1
            continue
        tr = [max(right.high - right.low, abs(right.high - left.close),
                  abs(right.low - left.close))
              for left, right in zip(window, window[1:])]
        atr = sum(tr[-14:]) / 14
        if atr <= 0 or not isfinite(atr):
            missing["invalid_atr"] = missing.get("invalid_atr", 0) + 1
            continue
        features = {
            "side_sell": float(trade.direction == "SELL"),
            "spread_atr": window[-1].spread_points * 0.01 / atr,
            "atr_pct": atr / window[-1].close,
            "ret_4_atr": (window[-1].close - window[-5].close) / atr,
            "ret_12_atr": (window[-1].close - window[-13].close) / atr,
            "range_12_atr": (max(bar.high for bar in window[-12:])
                              - min(bar.low for bar in window[-12:])) / atr,
        }
        rows.append({"number": trade.number, "features": features,
                     "full_stop": int("/ stop_loss" in outcome["reason"]),
                     "net_units": outcome["net_units"]})
    return rows, missing


def auc(labels, probabilities):
    pos = [p for y, p in zip(labels, probabilities) if y]
    neg = [p for y, p in zip(labels, probabilities) if not y]
    if not pos or not neg:
        return None
    return sum((a > b) + 0.5 * (a == b) for a in pos for b in neg) / (len(pos) * len(neg))


def evaluate(rows, *, holdout_count: int = 20):
    if holdout_count < 1 or len(rows) < 40 + holdout_count:
        raise ValueError("need at least 40 earlier trades and a separate holdout")
    rows = sorted(rows, key=lambda row: row["number"])
    train, holdout = rows[:-holdout_count], rows[-holdout_count:]
    labels = [row["full_stop"] for row in train]
    model = train_binary_logistic([row["features"] for row in train], labels, FEATURES)
    truth = [row["full_stop"] for row in holdout]
    probabilities = [model.predict_proba(row["features"]) for row in holdout]
    prevalence = sum(labels) / len(labels)
    positives, negatives = sum(truth), len(truth) - sum(truth)
    predicted = [int(p >= 0.5) for p in probabilities]
    balanced = (0.5 * (sum(a and b for a, b in zip(truth, predicted)) / positives
                       + sum(not a and not b for a, b in zip(truth, predicted)) / negatives)
                if positives and negatives else None)
    flagged = [row for row, p in zip(holdout, probabilities) if p >= 0.5]
    return {"covered": len(rows), "earlier_train": len(train),
            "earlier_stop_losses": sum(labels), "later_holdout": len(holdout),
            "later_stop_losses": positives, "auc": round(auc(truth, probabilities), 4)
            if auc(truth, probabilities) is not None else None,
            "balanced_accuracy_at_0_5": round(balanced, 4) if balanced is not None else None,
            "brier": round(sum((p-y)**2 for p, y in zip(probabilities, truth)) / len(truth), 4),
            "constant_training_prevalence_brier": round(
                sum((prevalence-y)**2 for y in truth) / len(truth), 4),
            "flagged_at_0_5": len(flagged),
            "flagged_actual_stop_losses": sum(row["full_stop"] for row in flagged),
            "limitations": [
                "This is not the live risk-role model: entry-time Chronos features are unavailable in the uploaded data.",
                "Only actually executed trades are labeled; rejecting trades changes later opportunities.",
                "Report timestamps are minute precision; all features end before the reported entry minute.",
                "Small holdout and one market period do not establish a profitable veto.",
            ]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", required=True)
    parser.add_argument("--m5-csv", required=True)
    parser.add_argument("--broker-utc-offset-seconds", type=int, required=True)
    parser.add_argument("--holdout-count", type=int, default=20)
    args = parser.parse_args()
    report = Path(args.report).read_text(encoding="utf-8")
    trades = read_trades(args.report, broker_utc_offset=args.broker_utc_offset_seconds)
    outcomes = {row["number"]: row for row in parse_report(report)}
    rows, missing = examples(read_m5(args.m5_csv), trades, outcomes)
    print(json.dumps({"input_utc_trades": len(trades), "excluded": missing,
                      **evaluate(rows, holdout_count=args.holdout_count)}, indent=2))


if __name__ == "__main__":
    main()
