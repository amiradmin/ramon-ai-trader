#!/usr/bin/env bash
# Install diagnostic-only STRUCTURE logs into the customized PostgreSQL server.
set -Eeuo pipefail
cd "${1:-$HOME/Documents/Presentation/ramon-ai-trader}"
mkdir -p backups/structure-log
stamp=$(date -u +%Y%m%dT%H%M%SZ)
backup="backups/structure-log/server.py.$stamp"
cp src/ramon/server.py "$backup"
restore() { cp "$backup" src/ramon/server.py; echo "Restored $backup" >&2; }
trap restore ERR
python3 - <<'PY'
from pathlib import Path
p = Path("src/ramon/server.py")
s = p.read_text()
if 'RAMON_STRUCTURE"\n' in s or 'structure_log_state = (' in s:
    print("Structure diagnostic already present")
    raise SystemExit(0)
anchor = '                sample_key = uuid4().hex[:16]'
if s.count(anchor) != 1 or 'apply_live_opportunity(' not in s:
    raise SystemExit("Unexpected server layout; aborting without changing live code")
block = '''                # Diagnostics only; never modifies the trade response.
                structure_log_state = (
                    str(response.get("opportunity_setup", "NONE")),
                    str(response.get("opportunity_decision", "WAIT")),
                    str(response.get("decision", "WAIT")),
                    str(response.get("opportunity_live_reason", "unknown")),
                )
                if getattr(self.server, "_structure_log_state", None) != structure_log_state:
                    self.server._structure_log_state = structure_log_state
                    print(
                        "RAMON_STRUCTURE"
                        + " setup=" + structure_log_state[0]
                        + " candidate=" + structure_log_state[1]
                        + " final=" + structure_log_state[2]
                        + " reason=" + structure_log_state[3],
                        flush=True,
                    )
'''
p.write_text(s.replace(anchor, block + anchor))
PY
python3 -m py_compile src/ramon/server.py
trap - ERR
echo "DIAGNOSTIC PATCH READY. Backup: $backup"
echo "Requires restarting only the model process to reload the Python code."
