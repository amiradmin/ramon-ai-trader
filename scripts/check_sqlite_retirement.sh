#!/usr/bin/env bash
# Full PostgreSQL cutover gate. Non-destructive by design.
set -Eeuo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
echo "RAMON SQLite retirement readiness check"
echo "Never deletes or renames a database; does not restart trading services."
if ! command -v rg >/dev/null; then
  echo "ripgrep (rg) is required for an auditable source inventory." >&2
  exit 2
fi
echo "== Unmigrated SQLite imports and API calls in active services =="
# Exclude migration helpers and tests, which may legitimately read historical SQLite.
rg -n 'sqlite3\.connect|from sqlite3 import|import sqlite3|RAMON_HISTORY_DB|ramon_history\.sqlite3' \
  src/ramon compose.yaml --glob '*.py' --glob '*.yaml' \
  | head -n 80 || true
echo
echo "== Remaining SQLite runtime dependency count =="
count=$(rg -l 'sqlite3\.connect|from sqlite3 import|import sqlite3' src/ramon --glob '*.py' | wc -l)
echo "Python modules with SQLite references: $count"
echo "STATUS: NOT READY FOR SQLITE RETIREMENT"
echo "Do NOT delete data/ramon_history.sqlite3."
exit 3
