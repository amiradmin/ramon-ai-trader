#!/usr/bin/env python3
"""Conservative static inventory of remaining SQLite calls in runtime modules.

Read-only. A hit is a migration work item, not proof a live PG request used SQLite.
"""
from pathlib import Path
import ast
ROOT=Path(__file__).resolve().parent.parent
MODULES=[
"src/ramon/monitor.py","src/ramon/opportunities.py","src/ramon/history.py",
"src/ramon/server.py","src/ramon/train_roles.py","src/ramon/mtf_collector.py",
"src/ramon/reversal_daily_report.py","src/ramon/v2_mt5_execution_health.py",
"src/ramon/trade_outbox.py","src/ramon/training_dashboard.py"
]
count=0
for filename in MODULES:
    path=ROOT/filename
    if not path.exists():continue
    tree=ast.parse(path.read_text(encoding="utf-8"),filename=filename)
    parents={}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):parents[child]=node
    for node in ast.walk(tree):
        if not isinstance(node,ast.Call):continue
        f=node.func
        if not (isinstance(f,ast.Attribute) and f.attr=="connect" and isinstance(f.value,ast.Name) and f.value.id=="sqlite3"):continue
        funcs=[];p=node
        while p in parents:
            p=parents[p]
            if isinstance(p,(ast.FunctionDef,ast.AsyncFunctionDef)):funcs.append(p.name)
        count+=1
        print(f"SQLITE_CALLSITE {filename}:{node.lineno} function={','.join(funcs) or '<module>'}")
print("TOTAL_SQLITE_CALLSITES",count)
print("RETIREMENT_NOT_APPROVED: each listed callsite needs PG route review or explicit archival-only classification.")
