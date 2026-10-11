"""Offline No-Trade candidate evaluation against realized net-R outcomes.

Input rows are independent, already-closed trade opportunities labeled at their
historical decision time. This module NEVER produces a live entry/exit signal.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path

from .trade_audit import summarize


def evaluate_shadow(rows: list[dict], *, veto_field: str = "candidate_veto") -> dict:
    """Compare identical outcomes under the original and candidate abstention policy.

    Requires explicit observed NET R (not gross P&L). Candidate veto must have
    been recorded at decision time. This is a descriptive replay, not OOS proof.
    """
    seen: set[str] = set()
    baseline: list[float] = []
    accepted: list[float] = []
    vetoed: list[float] = []
    for row in rows:
        key = str(row.get("opportunity_id") or "").strip()
        if not key or key in seen:
            raise ValueError("missing or duplicate opportunity_id")
        seen.add(key)
        value = float(row["net_r"])
        if not math.isfinite(value):
            raise ValueError("invalid net_r")
        flag = str(row.get(veto_field, "")).strip().lower()
        if flag not in ("0", "1", "true", "false"):
            raise ValueError("candidate_veto must be explicitly 0/1")
        veto = flag in ("1", "true")
        baseline.append(value)
        (vetoed if veto else accepted).append(value)
    if not rows:
        raise ValueError("empty evaluation")
    return {
        "mode": "OFFLINE_SHADOW_ONLY",
        "candidate_veto_field": veto_field,
        "baseline": summarize(baseline),
        "candidate_accepted": summarize(accepted),
        "candidate_vetoed": summarize(vetoed),
        "vetoed_winners": sum(v > 0 for v in vetoed),
        "vetoed_losers": sum(v < 0 for v in vetoed),
        "skipped_opportunity_rate": round(len(vetoed) / len(baseline), 6),
        "realized_net_r_difference_if_vetoed": round(-sum(vetoed), 6),
        "limitations": [
            "Labels for skipped historical opportunities require independent replay;",
            "Selection bias: never infer advantage from executed trades alone;",
            "Candidate veto must be captured at decision time, not retrospectively;",
            "Cannot infer true fill/slippage or probability of TP-before-SL;",
            "Require separate chronological holdout and uncertainty analysis;",
            "Does not connect to or alter live Ramon decisions.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv_path", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    with args.csv_path.open(encoding="utf-8-sig", newline="") as stream:
        result = evaluate_shadow(list(csv.DictReader(stream)))
    report = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(report, encoding="utf-8")
    else:
        print(report)


if __name__ == "__main__":
    main()
