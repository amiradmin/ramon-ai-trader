"""Research-only, chronological purged/embargoed evaluation windows.

A sample interval is [decision_utc, label_end_utc]. This helper never trains
a model or modifies decisions; callers must measure metrics only on test rows.
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path


def chronological_folds(rows: list[dict], *, folds: int = 3,
                        embargo_seconds: int = 3600) -> dict:
    if folds < 1 or embargo_seconds < 0:
        raise ValueError("invalid walk forward configuration")
    samples = []
    used = set()
    for row in rows:
        key = str(row["sample_key"])
        start, end = int(row["decision_utc"]), int(row["label_end_utc"])
        if not key or key in used or start <= 0 or end < start:
            raise ValueError("invalid sample, label interval, or duplicate key")
        used.add(key)
        samples.append((start, end, key))
    samples.sort()
    n = len(samples)
    # Strictly expanding training. Last (folds) blocks are holdouts; the
    # first block is training only. Require nonempty blocks and no guesses.
    if n < 2 * (folds + 1):
        raise ValueError("not enough samples for requested folds")
    boundaries = [(n * i) // (folds + 1) for i in range(folds + 2)]
    output = []
    for fold in range(1, folds + 1):
        lo, hi = boundaries[fold], boundaries[fold + 1]
        test = samples[lo:hi]
        if not test:
            raise ValueError("empty fold")
        first_test_time = test[0][0]
        cutoff = first_test_time - embargo_seconds
        # Labels for train must be fully known before the embargo cutoff.
        # Also exclude any training item with start >= test start.
        train = [row for row in samples[:lo] if row[1] < cutoff]
        output.append({
            "fold": fold,
            "training_keys": [r[2] for r in train],
            "test_keys": [r[2] for r in test],
            "training_count": len(train),
            "test_count": len(test),
            "purged_count": lo - len(train),
            "test_start_utc": first_test_time,
            "effective_train_label_end_before_utc": cutoff,
            "ready": len(train) > 0,
        })
    return {"mode": "OFFLINE_PURGED_WALK_FORWARD_SPLITS_ONLY",
            "samples": n, "folds": output,
            "limitations": [
                "Splits alone do not establish out-of-sample edge or performance.",
                "Do not tune features or thresholds on held-out results.",
                "Groups of overlapping/correlated decisions require further cluster-aware validation.",
            ]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=Path, required=True)
    parser.add_argument("--folds", type=int, default=3)
    parser.add_argument("--embargo-seconds", type=int, default=3600)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    with args.samples.open(newline="", encoding="utf-8-sig") as stream:
        rows = list(csv.DictReader(stream))
    result = chronological_folds(rows, folds=args.folds,
                                 embargo_seconds=args.embargo_seconds)
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered)


if __name__ == "__main__":
    main()
