#!/usr/bin/env python3
"""Read-only direction trace for selected Ramon trades.

Run inside the model container (where /data/ramon_history.sqlite3 is mounted):
    python /tmp/trace_direction_trades.py --db /data/ramon_history.sqlite3
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

TARGETS = {413: "50380302249", 415: "50380325780", 416: "50380334890", 424: "50380396738", 426: "50380409327"}
KEYS = (
    "decision", "reason", "direction", "final_decision", "market_direction",
    "forecast_direction", "intrabar_direction", "intrabar_confirmed",
    "ai_trend_direction", "buy_success_probability", "sell_success_probability",
    "ai_engine_v2_enabled", "ai_engine_v2_active", "ai_engine_v2_candidate_direction",
    "ai_engine_v2_direction_source", "ai_engine_v2_quality_margin",
    "ai_engine_v2_block", "ai_engine_v2_score", "regime", "regime_name",
    "allowed_directions", "direction_confirmation", "direction_conflict",
)

def parse_json(value):
    if not value:
        return {}
    try:
        result = json.loads(value)
        return result if isinstance(result, dict) else {"value": result}
    except (TypeError, ValueError):
        return {"raw": str(value)[:2000]}

def columns(con, table):
    return {row[1] for row in con.execute(f"PRAGMA table_info({table})")}

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="/data/ramon_history.sqlite3")
    parser.add_argument("--numbers", type=int, nargs="+", default=list(TARGETS),
                        help="1-based trade numbers in performance report order")
    args = parser.parse_args()
    db = Path(args.db).expanduser().resolve()
    if not db.is_file():
        parser.error(f"database not found: {db}")
    with sqlite3.connect(db.as_uri() + "?mode=ro", uri=True) as con:
        con.row_factory = sqlite3.Row
        tc = columns(con, "trade_outcomes")
        sc = columns(con, "decision_samples")
        print("Database:", db)
        print("trade_outcomes columns:", ", ".join(sorted(tc)))
        if not {"sample_key", "opened"}.issubset(tc):
            parser.error("trade_outcomes schema cannot be matched safely")
        for number in args.numbers:
            print("\n" + "=" * 70)
            print("REPORT ROW:", number)
            suffix = TARGETS.get(number)
            if suffix is None:
                print("UNKNOWN REPORT ROW NUMBER")
                continue
            matches = con.execute(
                "SELECT * FROM trade_outcomes WHERE trade_key LIKE ?",
                ("%:" + suffix,),
            ).fetchall()
            if len(matches) != 1:
                print(f"Expected one matching trade for ticket {suffix}; got {len(matches)}")
                continue
            trade = dict(matches[0])
            print("TRADE:", json.dumps(trade, ensure_ascii=False, default=str))
            key = trade.get("sample_key")
            if not key:
                print("NO SAMPLE KEY")
                continue
            sample = con.execute(
                "SELECT * FROM decision_samples WHERE sample_key=?", (key,)
            ).fetchone()
            if sample is None:
                print("NO JOINED DECISION SAMPLE")
                continue
            sample = dict(sample)
            print("DECISION:", json.dumps(sample, ensure_ascii=False, default=str))
            for field in ("base_decision", "final_decision", "regime_features",
                          "entry_features", "meta_base_features", "model_metadata"):
                if field in sc:
                    print(field.upper(), json.dumps(parse_json(sample.get(field)), ensure_ascii=False))
            audit_tables = {r[0] for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            if "inference_audit" in audit_tables:
                audit = con.execute(
                    "SELECT response_json, provenance_json FROM inference_audit WHERE sample_key=?",
                    (key,),
                ).fetchone()
                if audit:
                    response = parse_json(audit["response_json"])
                    print("INFERENCE_RESPONSE:", json.dumps(response, ensure_ascii=False))
                    print("INFERENCE_PROVENANCE:", audit["provenance_json"])
                    print("DIRECTION_FIELDS:", json.dumps(
                        {k: response.get(k) for k in KEYS if k in response},
                        ensure_ascii=False))
                else:
                    print("NO INFERENCE AUDIT FOR SAMPLE KEY")
            else:
                print("NO INFERENCE AUDIT TABLE")
        print("\nMatched by position ticket suffix from the uploaded report.")
        print("Censored/manual exits cannot validate the entry direction.")

if __name__ == "__main__":
    main()
