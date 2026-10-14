"""Read-only readiness check for Ramon's Logistic role training.

Uses the same PostgreSQL selection predicate as the trainer.
Never calls train_bundle or model promotion.
"""
from __future__ import annotations
import argparse
import json
import os
from collections import Counter

from .postgres_training import TRAINING_EXAMPLES_SQL

def summarize(rows: list[tuple]) -> dict:
    # Columns align with TRAINING_EXAMPLES_SQL.
    records = sorted(((int(r[0]), int(r[1]), int(float(r[6]) > 0), str(r[8]))
                      for r in rows), key=lambda x: x[0])
    n = len(records)
    result = {"eligible": n, "minimum_samples": 500,
              "remaining_to_500": max(0, 500-n),
              "wins": sum(r[2] for r in records),
              "losses": sum(1-r[2] for r in records),
              "by_direction": dict(Counter(r[3] for r in records))}
    if n < 100:
        result["state"] = "INSUFFICIENT_FOR_TEMPORAL_WINDOWS"
        return result
    meta_start = records[n//2][0]
    holdout_start = records[n*8//10][0]
    base = [r for r in records if r[0] < meta_start and r[1] < meta_start]
    meta = [r for r in records if meta_start <= r[0] < holdout_start and r[1] < holdout_start]
    holdout = [r for r in records if r[0] >= holdout_start]
    def stats(part):
        return {"count": len(part), "wins": sum(r[2] for r in part),
                "losses": sum(1-r[2] for r in part),
                "buy": sum(r[3]=="BUY" for r in part),
                "sell": sum(r[3]=="SELL" for r in part)}
    result["windows"] = {"base": stats(base), "meta": stats(meta), "holdout": stats(holdout)}
    result["base_roles_class_gate"] = len(base)>=40 and min(stats(base)["wins"],stats(base)["losses"])>=10
    result["meta_class_gate"] = len(meta)>=40 and min(stats(meta)["wins"],stats(meta)["losses"])>=10
    result["promotion_possible_now"] = False  # full gate requires unseen holdout evaluation
    result["state"] = "BELOW_500_SAMPLE_GATE" if n < 500 else "NEEDS_TRAINER_VALIDATION"
    return result

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol",default="XAUUSD_l")
    parser.add_argument("--model",default="autogluon/chronos-2-small")
    args=parser.parse_args()
    dsn=os.getenv("RAMON_PG_TRAINING_DSN")
    if not dsn:
        parser.error("RAMON_PG_TRAINING_DSN must be supplied securely")
    import psycopg
    with psycopg.connect(dsn, connect_timeout=5) as conn:
        with conn.cursor() as cur:
            cur.execute("BEGIN TRANSACTION READ ONLY")
            cur.execute("SET LOCAL statement_timeout='10000ms'")
            cur.execute(TRAINING_EXAMPLES_SQL,(args.symbol,args.model))
            rows=cur.fetchall()
    report={"mode":"READ_ONLY","symbol":args.symbol,"model":args.model,**summarize(rows)}
    print(json.dumps(report,ensure_ascii=False,indent=2))

if __name__=="__main__":
    main()
