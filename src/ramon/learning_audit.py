"""Read-only quality audit for Ramon decision and trade learning records.

No labels are generated or model training triggered here.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

MANUAL_REASONS = {"DEAL_REASON_CLIENT", "DEAL_REASON_MOBILE", "DEAL_REASON_WEB"}
REQUIRED_SAMPLE = ("sample_key", "captured", "symbol", "direction", "mid", "spread", "atr", "regime_features", "entry_features")
REQUIRED_OUTCOME = ("trade_key", "sample_key", "symbol", "direction", "opened", "closed", "net_units", "net_r", "exit_reason")


def _table_columns(con: sqlite3.Connection, table: str) -> set[str]:
    exists = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
    return {r[1] for r in con.execute(f"PRAGMA table_info({table})")} if exists else set()


def _count(con: sqlite3.Connection, sql: str, params: tuple = ()) -> int:
    return int(con.execute(sql, params).fetchone()[0])


def audit(db: str | Path, symbol: str = "XAUUSD_l") -> dict:
    path = Path(db).expanduser().resolve()
    result = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "symbol": symbol, "available": False, "status": "DB_MISSING",
        "schema": {}, "decisions": {}, "trades": {}, "training": {},
        "issues": [], "notes": [
            "Read-only audit; no orders, labels, training or policy changes.",
            "Manual intervention is not proof of a better counterfactual result.",
            "A learnable exit label is not proof of sufficient sample size or absence of leakage.",
        ],
    }
    if not path.is_file():
        result["issues"].append("History database not found")
        return result
    try:
        with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=2) as con:
            con.execute("PRAGMA query_only=ON")
            con.execute("BEGIN")
            s, t = _table_columns(con, "decision_samples"), _table_columns(con, "trade_outcomes")
            result["schema"] = {
                "decision_samples": sorted(s), "trade_outcomes": sorted(t),
                "missing_decision_columns": sorted(set(REQUIRED_SAMPLE) - s),
                "missing_trade_columns": sorted(set(REQUIRED_OUTCOME) - t),
            }
            if not s or not t:
                result["status"] = "SCHEMA_INCOMPLETE"
                result["issues"].append("Required decision_samples or trade_outcomes table missing")
                return result
            if result["schema"]["missing_decision_columns"] or result["schema"]["missing_trade_columns"]:
                result["status"] = "SCHEMA_INCOMPLETE"
                result["issues"].append("Required columns missing; no join attempted")
                return result
            result["available"] = True
            decisions = _count(con, "SELECT COUNT(*) FROM decision_samples WHERE symbol=?", (symbol,))
            outcomes = _count(con, "SELECT COUNT(*) FROM trade_outcomes WHERE symbol=?", (symbol,))
            result["decisions"] = {
                "total": decisions,
                "by_decision": dict(con.execute(
                    "SELECT COALESCE(final_decision,base_decision,'UNKNOWN'), COUNT(*) FROM decision_samples WHERE symbol=? GROUP BY COALESCE(final_decision,base_decision,'UNKNOWN')",
                    (symbol,)).fetchall()) if "final_decision" in s else dict(con.execute(
                        "SELECT base_decision, COUNT(*) FROM decision_samples WHERE symbol=? GROUP BY base_decision",
                        (symbol,)).fetchall()),
                "missing_sample_key": _count(con, "SELECT COUNT(*) FROM decision_samples WHERE symbol=? AND (sample_key IS NULL OR TRIM(sample_key)='')", (symbol,)),
                "invalid_quote_or_atr": _count(con, "SELECT COUNT(*) FROM decision_samples WHERE symbol=? AND (mid<=0 OR spread<0 OR atr<=0)", (symbol,)),
                "duplicate_sample_keys": _count(con, "SELECT COUNT(*) FROM (SELECT sample_key FROM decision_samples WHERE symbol=? AND sample_key IS NOT NULL AND sample_key<>'' GROUP BY sample_key HAVING COUNT(*)>1)", (symbol,)),
            } if "sample_key" in s else {"total": decisions}
            join = """FROM trade_outcomes t LEFT JOIN decision_samples d
                      ON t.sample_key=d.sample_key AND t.symbol=d.symbol WHERE t.symbol=?"""
            matched = _count(con, "SELECT COUNT(*) " + join + " AND d.id IS NOT NULL", (symbol,))
            by_exit = dict(con.execute(
                "SELECT COALESCE(exit_reason,'UNKNOWN'),COUNT(*) FROM trade_outcomes WHERE symbol=? GROUP BY exit_reason",
                (symbol,)).fetchall())
            manual = sum(v for k, v in by_exit.items() if k in MANUAL_REASONS)
            by_status = dict(con.execute(
                "SELECT COALESCE(training_status,'UNKNOWN'),COUNT(*) FROM trade_outcomes WHERE symbol=? GROUP BY training_status",
                (symbol,)).fetchall()) if "training_status" in t else {}
            source = dict(con.execute(
                "SELECT COALESCE(NULLIF(TRIM(entry_source),''),'UNKNOWN'),COUNT(*) FROM trade_outcomes WHERE symbol=? GROUP BY COALESCE(NULLIF(TRIM(entry_source),''),'UNKNOWN')",
                (symbol,)).fetchall()) if "entry_source" in t else {"UNKNOWN": outcomes}
            result["trades"] = {
                "total": outcomes, "matched_decisions": matched, "unmatched_decisions": outcomes-matched,
                "join_rate_pct": round(100*matched/outcomes,2) if outcomes else None,
                "by_exit_reason": by_exit, "manual_exit_count": manual,
                "by_entry_source": source, "by_training_status": by_status,
                "invalid_chronology": _count(con, "SELECT COUNT(*) FROM trade_outcomes WHERE symbol=? AND (opened<=0 OR closed<opened)", (symbol,)),
                "invalid_risk": _count(con, "SELECT COUNT(*) FROM trade_outcomes WHERE symbol=? AND initial_risk_units<=0", (symbol,)) if "initial_risk_units" in t else None,
                "decision_after_entry": _count(con, "SELECT COUNT(*) "+join+" AND d.id IS NOT NULL AND d.captured>t.opened", (symbol,)),
                "symbol_or_direction_conflicts": _count(con, "SELECT COUNT(*) "+join+" AND d.id IS NOT NULL AND d.direction<>t.direction", (symbol,)),
            }
            learnable = by_status.get("LEARNABLE", 0)
            result["training"] = {
                "exit_label_eligible": learnable,
                "exit_label_eligible_pct": round(100*learnable/outcomes,2) if outcomes else None,
                "manual_exit_censored": by_status.get("CENSORED_MANUAL", 0),
                "entry_source_known_pct": round(100*(outcomes-source.get("UNKNOWN",0))/outcomes,2) if outcomes else None,
                "ready_for_training": False,
                "readiness_reason": "Requires temporal holdout, independent-day coverage, provenance validation and leakage checks",
            }
            if not outcomes: result["issues"].append("No completed trade outcomes")
            if outcomes and matched < outcomes: result["issues"].append("Some trade outcomes do not link to their historical decision")
            if result["trades"]["decision_after_entry"]: result["issues"].append("Future decision timestamps detected: possible leakage or clock mismatch")
            if result["trades"]["symbol_or_direction_conflicts"]: result["issues"].append("Decision/trade direction mismatch")
            if result["trades"]["invalid_chronology"]: result["issues"].append("Invalid trade timestamps")
            if result["trades"]["invalid_risk"]: result["issues"].append("Missing or invalid initial risk")
            if result["decisions"]["invalid_quote_or_atr"]: result["issues"].append("Bad quote/ATR records")
            if not "entry_source" in t: result["issues"].append("Entry source provenance column unavailable")
            if source.get("UNKNOWN",0): result["issues"].append("Some trade entry sources are unknown")
            if not "training_status" in t: result["issues"].append("Training status column unavailable")
            result["status"] = "AUDIT_WARNINGS" if result["issues"] else "AUDIT_COMPLETE"
    except sqlite3.Error as exc:
        result["status"] = "DB_ERROR"
        result["issues"].append(f"SQLite {type(exc).__name__}")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only Ramon learning dataset audit")
    parser.add_argument("--db", default="data/ramon_history.sqlite3")
    parser.add_argument("--symbol", default="XAUUSD_l")
    args = parser.parse_args()
    print(json.dumps(audit(args.db, args.symbol), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
