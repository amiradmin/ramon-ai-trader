# Ramon V2 Position Lifecycle Audit (shadow/read-only)

The `research_events` table contains recorded position snapshots. This tool compares
successive observed values of `sl` and `tp_stage` per account and position.
It reports telemetry gaps and event kind counts without changing the live EA,
risk parameters or order state.

```bash
env -u PYTHONPATH PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  uv run --no-sync pytest -q tests/test_position_lifecycle_audit.py

uv run --no-sync python -m ramon.position_lifecycle_audit \
  --db data/ramon_history.sqlite3 \
  --symbol XAUUSD_l \
  --output data/research/position_lifecycle_audit.json
```

Review the output **locally** because position IDs and account identifiers can be
present. Do not commit or post raw reports containing account identifiers.
To share aggregate counts only, run:

```bash
python3 - <<'PY'
import json
from collections import Counter
p='data/research/position_lifecycle_audit.json'
r=json.load(open(p))
print('positions_with_events:', r['positions_with_events'])
print('telemetry_gap_events:', r['telemetry_gap_events'])
print('malformed_position_payloads:', r['malformed_position_payloads'])
print('observed_sl_transitions:', sum(len(p['observed_sl_transitions']) for p in r['positions']))
print('observed_tp_stage_transitions:', sum(len(p['observed_tp_stage_transitions']) for p in r['positions']))
print('position_change_coverage:', sum(p['has_position_change'] for p in r['positions']))
PY
```

Observed changes **do not prove** the EA actually issued or broker-confirmed a
stop modification; intervening changes may be missed. Events are ordered by
the broker timestamp and run sequence. Neither broker-time normalization nor
telemetry completeness is independently proven. `received_utc` may be a
batch-ingestion timestamp. Do not evaluate exit-policy effectiveness using
this report alone.

The documented user sample had 3888 events, 3533 quotes, 12 changes covering
three positions and 55 telemetry gaps; these figures were observed before
this code was run on the user's database, and are not validation of this tool.
