"""Read-only PostgreSQL report for direction conflicts.

Requires RAMON_PG_TRAINING_DSN, prints aggregate statistics only.
"""
from __future__ import annotations
import argparse
import json
import os
from .direction_conflict_research import summarize

QUERY = """
SELECT s.source->>'direction' AS snapshot,
       s.source->>'final_decision' AS final,
       t.source->>'direction' AS executed,
       COALESCE(t.source->>'entry_source','UNKNOWN') AS source,
       (t.source->>'net_r')::double precision AS net_r,
       (s.source->>'quote_time')::bigint AS quote_time,
       (t.source->>'opened')::bigint AS opened
FROM ramon_migration.training_trade_outcomes t
JOIN ramon_migration.training_decision_samples s
  ON s.sample_key = t.source->>'sample_key'
WHERE s.source->>'symbol' = %s
  AND t.source->>'symbol' = s.source->>'symbol'
  AND s.source->>'chronos_model' = %s
  AND t.source->>'training_status' = 'LEARNABLE'
  AND t.source->>'direction' IS DISTINCT FROM s.source->>'direction'
ORDER BY (s.source->>'quote_time')::bigint DESC
"""

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default="XAUUSD_l")
    parser.add_argument("--model", default="autogluon/chronos-2-small")
    args = parser.parse_args()
    dsn = os.environ.get("RAMON_PG_TRAINING_DSN")
    if not dsn:
        parser.error("RAMON_PG_TRAINING_DSN is not set")
    import psycopg
    with psycopg.connect(dsn, connect_timeout=5) as connection:
        with connection.cursor() as cursor:
            cursor.execute("BEGIN TRANSACTION READ ONLY")
            cursor.execute("SET LOCAL statement_timeout = '10000ms'")
            cursor.execute(QUERY, (args.symbol, args.model))
            names = [column.name for column in cursor.description]
            rows = [dict(zip(names, r)) for r in cursor.fetchall()]
    result = {"mode": "RESEARCH_ONLY", "execution_changes": False,
              "total_conflicts": len(rows), "groups": summarize(rows)}
    print(json.dumps(result, indent=2, ensure_ascii=False))

if __name__ == "__main__":
    main()
