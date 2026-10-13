"""Read-only Ramon Evaluation Lab: audit first, do not simulate unverified fills.

This module deliberately never changes an EA setting or executes a broker action.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
import math
from pathlib import Path
import sqlite3


AUTO_SOURCES = frozenset({"AUTO_RAMON", "RANGE_AUTO"})
MANUAL_SOURCES = frozenset({"HUMAN_ASSISTED", "DASHBOARD_MANUAL", "MANUAL"})


def _columns(db, table):
    return {row[1] for row in db.execute(f"PRAGMA table_info({table})")}


def _finite(value):
    try:
        x = float(value)
        return x if math.isfinite(x) else None
    except (ValueError, TypeError):
        return None


def evaluate(db_path, symbol="XAUUSD_l", limit=50000):
    path = Path(db_path).expanduser().resolve()
    if not path.is_file():
        return {"available": False, "reason": "HISTORY_DB_MISSING"}
    limit = min(100000, max(1, int(limit)))
    try:
        with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=1) as db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA query_only=ON")
            db.execute("BEGIN")
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not {"decision_samples", "trade_outcomes"}.issubset(tables):
                return {"available": False, "reason": "REQUIRED_TABLES_MISSING"}
            dc, tc = _columns(db, "decision_samples"), _columns(db, "trade_outcomes")
            required = {"sample_key", "symbol", "final_decision"}
            trade_required = {"sample_key", "symbol", "entry_source", "net_units", "net_r", "closed"}
            if not required.issubset(dc) or not trade_required.issubset(tc):
                return {"available": False, "reason": "REQUIRED_COLUMNS_MISSING",
                        "decision_missing": sorted(required-dc),
                        "trade_missing": sorted(trade_required-tc)}
            decisions = [dict(r) for r in db.execute(
                "SELECT sample_key,final_decision FROM decision_samples WHERE symbol=? "
                "ORDER BY rowid DESC LIMIT ?", (symbol, limit))]
            trade_cols = ["sample_key", "entry_source", "net_units", "net_r", "closed"]
            trade_cols += [c for c in ("trade_key", "manual_intervention", "trade_role",
                                       "exit_detail", "opened", "direction", "entry_ea_version")
                           if c in tc]
            trades = [dict(r) for r in db.execute(
                "SELECT "+",".join(trade_cols)+" FROM trade_outcomes WHERE symbol=? "
                "ORDER BY rowid DESC LIMIT ?", (symbol, limit))]
    except (sqlite3.Error, OSError) as exc:
        return {"available": False, "reason": "READ_FAILED", "error_type": type(exc).__name__}

    keys = Counter(str(d["sample_key"]) for d in decisions if d["sample_key"])
    duplicates = sum(n-1 for n in keys.values() if n>1)
    waits = sum(str(d["final_decision"]).upper()=="WAIT" for d in decisions)
    sourced = Counter(str(t.get("entry_source") or "UNKNOWN").upper() for t in trades)
    qualified, excluded, unmatched = [], Counter(), 0
    for t in trades:
        source = str(t.get("entry_source") or "").upper()
        if source not in AUTO_SOURCES:
            excluded["OTHER_OR_MANUAL_SOURCE"] += 1
            continue
        if "manual_intervention" not in t:
            excluded["INTERVENTION_COLUMN_MISSING"] += 1
            continue
        marker = t.get("manual_intervention")
        if marker not in (None, "", "NONE", "none", "false", "0", 0):
            excluded["RECORDED_MANUAL_INTERVENTION"] += 1
            continue
        if not _finite(t.get("closed")) or float(t["closed"]) <= 0:
            excluded["NOT_CLOSED"] += 1
            continue
        pnl, r = _finite(t["net_units"]), _finite(t["net_r"])
        if pnl is None or r is None:
            excluded["INVALID_PNL_OR_R"] += 1
            continue
        key = str(t.get("sample_key") or "")
        if not key or key not in keys:
            unmatched += 1
            excluded["NO_MATCHING_DECISION"] += 1
            continue
        # Absence of a recorded manual intervention is not evidence that none occurred.
        qualified.append((pnl, r, key, source))
    gains = sum(p for p, *_ in qualified if p > 0)
    losses = -sum(p for p, *_ in qualified if p < 0)
    total = sum(p for p, *_ in qualified)
    return {
        "available": True, "symbol": symbol, "read_only": True, "sample_limit": limit,
        "data_audit": {"decisions_scanned": len(decisions), "wait_decisions": waits,
                       "duplicate_decision_sample_keys": duplicates,
                       "trade_outcomes_scanned": len(trades),
                       "entry_sources": dict(sourced), "unmatched_auto_samples": unmatched,
                       "exclusion_reasons": dict(excluded)},
        "auto_trades": {"candidate_count": len(qualified), "net_units": round(total, 5),
                        "wins": sum(p>0 for p,*_ in qualified),
                        "losses": sum(p<0 for p,*_ in qualified),
                        "win_rate": (sum(p>0 for p,*_ in qualified)/len(qualified) if qualified else None),
                        "profit_factor": (gains/losses if losses>0 else None),
                        "mean_r": (sum(r for _,r,*_ in qualified)/len(qualified) if qualified else None),
                        "provenance": "PROVISIONAL_UNVERIFIED_MANUAL_ABSENCE",
                        "ready_for_training": False,
                        "reason": "Manual intervention absence and execution completeness not independently audited"},
        "wait_replay": {"status": "NOT_EVALUATED", "reason":
                        "Require time-aligned M1/ticks, executable bid/ask, intrabar SL/TP priority, "
                        "account costs and independent walk-forward folds before computing counterfactual P&L"},
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="data/ramon_history.sqlite3")
    parser.add_argument("--symbol", default="XAUUSD_l")
    parser.add_argument("--limit", type=int, default=50000)
    args = parser.parse_args()
    print(json.dumps(evaluate(args.db, args.symbol, args.limit), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
