#!/usr/bin/env python3
"""Fail-closed inventory of Ramon Compose services still needing PostgreSQL work.

Static audit only. Does not modify data, containers, or EA positions.
"""
from pathlib import Path
import re
import sys

ROOT=Path(__file__).resolve().parent.parent
sources={
    "model":["src/ramon/server.py","src/ramon/history.py"],
    "dashboard":["src/ramon/monitor.py","src/ramon/opportunities.py"],
    "market-dashboard":["src/ramon/market_dashboard.py","src/ramon/opportunities.py","src/ramon/monitor.py"],
    "training-dashboard":["src/ramon/training_dashboard.py","src/ramon/train_roles.py"],
    "mtf-collector":["src/ramon/mtf_collector.py"],
    "reversal-daily-report":["src/ramon/reversal_daily_report.py"],
    "v2-execution-health":["src/ramon/v2_mt5_execution_health.py"],
}
out={}
for service,files in sources.items():
    blockers=[]
    for name in files:
        s=(ROOT/name).read_text(encoding="utf-8")
        uses_sqlite=bool(re.search(r"sqlite3\.connect\s*\(|\bPRAGMA\b",s))
        has_pg="RAMON_STORAGE_BACKEND" in s or "RAMON_TRAINING_BACKEND" in s
        if uses_sqlite and not has_pg:
            blockers.append(name)
    out[service]=blockers
for service,blockers in out.items():
    print(f"{service}: {'NEEDS_PG_PORT' if blockers else 'REVIEW_RUNTIME_AND_TESTS'}")
    for filename in blockers: print(f"  - {filename}")
print("NO_CUTOVER_AUTHORIZATION: audit cannot prove active PG wiring or absence of SQLite runtime dependencies")
sys.exit(2 if any(out.values()) else 0)
