"""Purged train/calibration/test validation on observed clean Entry opportunities.

No active or shadow inference pointer is written. Thresholds are fixed in advance;
retained-opportunity P/L is not a simulation of new opportunities after a veto.
"""
from __future__ import annotations

import argparse
import json
from math import log
from pathlib import Path
import sqlite3

from .bundles import atomic_json
from .ensemble import ENTRY_FEATURES, train_binary_logistic
from .train_roles import fit_role, read_trade_examples, trade_metrics


def metrics(rows, probabilities):
    if not rows:
        return {"samples": 0}
    brier = sum((p-row.label)**2 for row, p in zip(rows, probabilities))/len(rows)
    bins = []
    for lower, upper in ((0, .2), (.2, .4), (.4, .6), (.6, .8), (.8, 1.000001)):
        paired = [(r, p) for r, p in zip(rows, probabilities) if lower <= p < upper]
        if paired:
            bins.append(dict(lower=lower, upper=min(upper, 1), samples=len(paired),
                             mean_probability=sum(p for _, p in paired)/len(paired),
                             actual_win_rate=sum(r.label for r, _ in paired)/len(paired)))
    return {"samples": len(rows), "brier": brier, "reliability_bins": bins}


def logit(value):
    p = min(max(value, 1e-6), 1-1e-6)
    return log(p/(1-p))


def validate_entry(examples):
    report = {"mode": "offline_entry_validation", "live_execution_effect": "NONE",
              "promotion_allowed": False, "eligible_samples": len(examples),
              "thresholds_fixed_before_test": [.5, .6, .7],
              "limitations": ["Executed, clean closed trades only; WAIT opportunities have no observed payoff.",
                              "Retained-opportunity results do not simulate replacement trades after a veto.",
                              "Historical EA policies differ; this is not evidence of live profitability."]}
    if len(examples) < 100:
        return {**report, "status": "waiting_for_data", "reason": "need >=100 eligible clean closed trades"}
    ordered = sorted(examples, key=lambda r: r.time)
    calibration_start = ordered[len(ordered)//2].time
    test_start = ordered[len(ordered)*3//4].time
    training = [r for r in ordered if r.time < calibration_start and r.label_end < calibration_start]
    calibration = [r for r in ordered if calibration_start <= r.time < test_start and r.label_end < test_start]
    test = [r for r in ordered if r.time >= test_start]
    report["windows"] = {"training": len(training), "calibration": len(calibration), "test": len(test),
                         "calibration_start_mt5": calibration_start, "test_start_mt5": test_start,
                         "clock": "recorded broker event timestamps, not relabeled as UTC",
                         "purged": len(ordered)-len(training)-len(calibration)-len(test)}
    try:
        model = fit_role(training, "entry", ENTRY_FEATURES)
    except (ValueError, KeyError, TypeError) as exc:
        return {**report, "status": "waiting_for_data", "reason": str(exc)}
    raw = [model.predict_proba(r.features["entry"]) for r in test]
    report["raw_test"] = metrics(test, raw)
    cal_prob = [model.predict_proba(r.features["entry"]) for r in calibration]
    calibrated = None
    labels = [r.label for r in calibration]
    if len(labels) >= 40 and min(labels.count(0), labels.count(1)) >= 10:
        calibrator = train_binary_logistic([{"logit": logit(p)} for p in cal_prob], labels, ("logit",))
        calibrated = [calibrator.predict_proba({"logit": logit(p)}) for p in raw]
        report["calibrated_test"] = metrics(test, calibrated)
    else:
        report["calibration_status"] = "unavailable: need >=40 calibration trades and >=10 of each class"
    report["baseline_observed_opportunities"] = trade_metrics(test, [True]*len(test))
    report["fixed_threshold_results"] = {}
    for name, probabilities in (("raw", raw), ("calibrated", calibrated)):
        if probabilities is None:
            continue
        for threshold in report["thresholds_fixed_before_test"]:
            accepted = [p >= threshold for p in probabilities]
            report["fixed_threshold_results"][f"{name}:{threshold}"] = {
                **trade_metrics(test, accepted),
                "rejected_winners": sum(r.label == 1 and not a for r, a in zip(test, accepted)),
                "rejected_losses": sum(r.label == 0 and not a for r, a in zip(test, accepted)),
            }
    return {**report, "status": "evaluated_unvalidated", "selected_threshold": None}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="/data/ramon_history.sqlite3")
    parser.add_argument("--symbol", default="XAUUSD_l")
    parser.add_argument("--model", default="autogluon/chronos-2-small")
    parser.add_argument("--active-model-file", default="/checkpoints/active_model.txt")
    parser.add_argument("--out", default="/data/research/entry-validation.json")
    args = parser.parse_args()
    checkpoint = Path(args.active_model_file)
    model_id = checkpoint.read_text().strip() if checkpoint.exists() else args.model
    with sqlite3.connect(Path(args.db).resolve().as_uri()+"?mode=ro", uri=True) as connection:
        examples = read_trade_examples(connection, args.symbol, model_id, exclude_manual_expert=True)
    report = validate_entry(examples)
    report.update(symbol=args.symbol, chronos_model=model_id)
    atomic_json(Path(args.out), report)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
