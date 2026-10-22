"""Build an auditable human-review dataset from Sahar's market opinions.

Human opinions are learning/review signals only. This module never changes live
execution and never invents counterfactual PnL for an opposite-direction opinion.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3


def _safe_json(value: str) -> dict:
    try:
        data = json.loads(value or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def load_human_review_rows(db: str | Path, symbol: str = "XAUUSD_l") -> list[dict[str, object]]:
    path = Path(db)
    if not path.exists():
        return []
    with sqlite3.connect(path) as con:
        con.row_factory = sqlite3.Row
        exists = con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='human_market_opinions'"
        ).fetchone()
        if not exists:
            return []
        rows = con.execute(
            """
            SELECT
                h.id, h.created_utc, h.reviewer, h.symbol, h.sample_key,
                h.signal_bar_time, h.opinion, h.confidence, h.note, h.snapshot_json,
                s.final_decision AS ramon_decision,
                s.base_decision AS ramon_base_decision,
                s.direction AS model_direction,
                s.atr, s.mid, s.spread, s.quote_time, s.chronos_model, s.bundle_id,
                t.direction AS trade_direction,
                t.net_r, t.net_units, t.exit_reason, t.training_status,
                t.opened, t.closed
            FROM human_market_opinions h
            LEFT JOIN decision_samples s ON s.sample_key=h.sample_key
            LEFT JOIN trade_outcomes t ON t.sample_key=h.sample_key
            WHERE h.symbol=?
            ORDER BY h.created_utc, h.id
            """,
            (symbol,),
        ).fetchall()

    result: list[dict[str, object]] = []
    for row in rows:
        item = dict(row)
        item["snapshot"] = _safe_json(str(item.pop("snapshot_json") or "{}"))
        human = str(item.get("opinion") or "").upper()
        ramon = str(item.get("ramon_decision") or "").upper()
        trade = str(item.get("trade_direction") or "").upper()
        pnl = item.get("net_r")
        item["ramon_agreement"] = int(bool(ramon) and human == ramon)
        item["outcome_available"] = int(pnl is not None)
        item["human_trade_alignment"] = int(
            human in {"BUY", "SELL"} and trade in {"BUY", "SELL"} and human == trade
        )
        item["human_wait_reviewable"] = int(
            human == "WAIT" and trade in {"BUY", "SELL"} and pnl is not None
        )
        item["human_opposite_direction"] = int(
            human in {"BUY", "SELL"} and trade in {"BUY", "SELL"} and human != trade
        )
        # Only score realized PnL where the human chose the same executed side.
        # Opposite-direction PnL is intentionally unknown rather than negated.
        item["aligned_trade_win"] = (
            int(float(pnl) > 0)
            if item["human_trade_alignment"] and pnl is not None
            else None
        )
        if item["human_wait_reviewable"]:
            item["wait_would_avoid_loss"] = int(float(pnl) < 0)
            item["wait_would_skip_win"] = int(float(pnl) > 0)
        else:
            item["wait_would_avoid_loss"] = None
            item["wait_would_skip_win"] = None
        result.append(item)
    return result


def build_human_review_report(rows: list[dict[str, object]]) -> dict[str, object]:
    total = len(rows)
    linked_decisions = sum(1 for r in rows if r.get("ramon_decision"))
    linked_outcomes = sum(int(r.get("outcome_available") or 0) for r in rows)
    agreements = sum(int(r.get("ramon_agreement") or 0) for r in rows)
    aligned = [r for r in rows if r.get("human_trade_alignment") and r.get("net_r") is not None]
    waits = [r for r in rows if r.get("human_wait_reviewable")]
    opposites = [r for r in rows if r.get("human_opposite_direction")]

    by_confidence: dict[str, dict[str, float | int]] = {}
    buckets: dict[int, list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        buckets[int(row.get("confidence") or 0)].append(row)
    for confidence, bucket in sorted(buckets.items()):
        b_aligned = [r for r in bucket if r.get("human_trade_alignment") and r.get("net_r") is not None]
        b_waits = [r for r in bucket if r.get("human_wait_reviewable")]
        by_confidence[str(confidence)] = {
            "samples": len(bucket),
            "ramon_agreement_rate": (
                sum(int(r.get("ramon_agreement") or 0) for r in bucket) / len(bucket)
                if bucket else 0.0
            ),
            "aligned_trade_samples": len(b_aligned),
            "aligned_trade_win_rate": (
                sum(int(r.get("aligned_trade_win") or 0) for r in b_aligned) / len(b_aligned)
                if b_aligned else 0.0
            ),
            "aligned_trade_net_r": sum(float(r.get("net_r") or 0.0) for r in b_aligned),
            "wait_reviewable_samples": len(b_waits),
            "wait_avoided_losses": sum(int(r.get("wait_would_avoid_loss") or 0) for r in b_waits),
            "wait_skipped_wins": sum(int(r.get("wait_would_skip_win") or 0) for r in b_waits),
        }

    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": "human_market_opinions joined to decision_samples and trade_outcomes",
        "reviewer": "Sahar",
        "samples": total,
        "linked_decisions": linked_decisions,
        "linked_outcomes": linked_outcomes,
        "ramon_agreements": agreements,
        "ramon_agreement_rate": agreements / linked_decisions if linked_decisions else 0.0,
        "aligned_trade_samples": len(aligned),
        "aligned_trade_wins": sum(int(r.get("aligned_trade_win") or 0) for r in aligned),
        "aligned_trade_win_rate": (
            sum(int(r.get("aligned_trade_win") or 0) for r in aligned) / len(aligned)
            if aligned else 0.0
        ),
        "aligned_trade_net_r": sum(float(r.get("net_r") or 0.0) for r in aligned),
        "wait_reviewable_samples": len(waits),
        "wait_avoided_losses": sum(int(r.get("wait_would_avoid_loss") or 0) for r in waits),
        "wait_skipped_wins": sum(int(r.get("wait_would_skip_win") or 0) for r in waits),
        "opposite_direction_unscored": len(opposites),
        "confidence_breakdown": by_confidence,
        "training_policy": {
            "live_execution_effect": "none",
            "human_labels": "retained for auxiliary/shadow learning and audit",
            "opposite_direction_counterfactual": "not_inferred",
            "minimum_recommended_before_model_use": 100,
        },
    }


def write_review_artifacts(
    db: str | Path,
    *,
    symbol: str,
    report_path: str | Path,
    dataset_path: str | Path,
) -> dict[str, object]:
    rows = load_human_review_rows(db, symbol)
    report = build_human_review_report(rows)
    report_file = Path(report_path)
    dataset_file = Path(dataset_path)
    report_file.parent.mkdir(parents=True, exist_ok=True)
    dataset_file.parent.mkdir(parents=True, exist_ok=True)
    report_file.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    with dataset_file.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="/data/ramon_history.sqlite3")
    parser.add_argument("--symbol", default="XAUUSD_l")
    parser.add_argument(
        "--report", default="/checkpoints/ensemble/human_review_report.json"
    )
    parser.add_argument(
        "--dataset", default="/checkpoints/ensemble/human_review_samples.jsonl"
    )
    args = parser.parse_args()
    report = write_review_artifacts(
        args.db,
        symbol=args.symbol,
        report_path=args.report,
        dataset_path=args.dataset,
    )
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
