"""Read-only PostgreSQL training migration audit. No schema changes or model promotion."""
from __future__ import annotations
import argparse
import json
import os

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--dsn", default=os.getenv("RAMON_PG_TRAINING_DSN", ""))
    p.add_argument("--timeout-ms", type=int, default=5000)
    a = p.parse_args()
    if not a.dsn:
        p.error("RAMON_PG_TRAINING_DSN is missing")
    import psycopg
    report = {"read_only": True, "training_ready": False}
    with psycopg.connect(a.dsn, connect_timeout=5) as conn:
        with conn.cursor() as cur:
            cur.execute("BEGIN TRANSACTION READ ONLY")
            cur.execute("SELECT set_config('statement_timeout', %s, true)", (str(a.timeout_ms),))
            cur.execute("""
                SELECT to_regclass('ramon_migration.training_decision_samples')::text,
                       to_regclass('ramon_migration.training_trade_outcomes')::text
            """)
            tables = cur.fetchone()
            report["training_tables"] = tables
            if not all(tables):
                print(json.dumps(report, indent=2))
                return
            cur.execute("""
                SELECT relname, reltuples::bigint AS estimated_rows
                FROM pg_class
                WHERE oid IN (
                    'ramon_migration.training_decision_samples'::regclass,
                    'ramon_migration.training_trade_outcomes'::regclass)
                ORDER BY relname
            """)
            report["estimated_rows_from_pg_stats"] = dict(cur.fetchall())
            cur.execute("""
                SELECT tablename,indexname,indexdef
                FROM pg_indexes WHERE schemaname='ramon_migration'
                  AND tablename IN ('training_decision_samples','training_trade_outcomes')
                ORDER BY tablename,indexname
            """)
            report["indexes"] = [
                dict(zip(("table", "name", "definition"), row)) for row in cur.fetchall()
            ]
            cur.execute("""
                EXPLAIN (FORMAT JSON)
                SELECT count(*)
                FROM ramon_migration.training_decision_samples s
                JOIN ramon_migration.training_trade_outcomes t
                  ON t.source->>'sample_key' = s.source->>'sample_key'
                WHERE s.source->>'symbol'='XAUUSD_l'
                  AND s.source->>'chronos_model'='autogluon/chronos-2-small'
                  AND t.source->>'training_status'='LEARNABLE'
            """)
            plan = cur.fetchone()[0]
            report["join_plan"] = plan[0]["Plan"] if isinstance(plan, list) else plan
            report["note"] = "Planner estimates are not exact counts. EXPLAIN is not ANALYZE."
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))

if __name__ == "__main__":
    main()
