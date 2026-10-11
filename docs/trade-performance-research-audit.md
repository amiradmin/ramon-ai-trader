# Ramon offline exit telemetry and trade-performance audit

The research-only PR contains two complementary offline reports:

1. `position_lifecycle_audit` summarizes observed SL transitions, separately reporting repeated-snapshot ("stable observed") transitions and transient contradictory snapshot readings. **Stable is not broker-confirmed**, and neither the chronological broker clock nor full capture is proven.
2. `trade_outcome_audit` groups realized R and recorded net account units by UTC entry hour, BUY/SELL, EA version, trade role, exit reason/detail, manual close, spread and ATR buckets. Groups are descriptive, not policy recommendations; the data may have regime-dependent sampling, versions and manual intervention.

Run on the HP-Mini after pulling the branch:

```bash
env -u PYTHONPATH PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  uv run --no-sync pytest -q \
  tests/test_position_lifecycle_audit.py tests/test_trade_outcome_audit.py

uv run --no-sync python -m ramon.position_lifecycle_audit \
  --db data/ramon_history.sqlite3 \
  --output data/research/position_lifecycle_audit.json

uv run --no-sync python -m ramon.trade_outcome_audit \
  --db data/ramon_history.sqlite3 \
  --output data/research/trade_outcome_audit.json
```

Position lifecycle reports include account and position IDs. Keep local reports
out of GitHub and do not share them unredacted.

Summary of key metrics without identifiers:

```bash
python3 - <<'PY'
import json
p=json.load(open('data/research/position_lifecycle_audit.json'))
t=json.load(open('data/research/trade_outcome_audit.json'))
print('Position snapshots:',p['positions_with_events'])
print('Observed SL transitions:',sum(len(x['observed_sl_transitions']) for x in p['positions']))
print('Repeated-snapshot SL transitions:',sum(len(x['stable_observed_sl_transitions']) for x in p['positions']))
print('Transient contradictions:',sum(len(x['transient_sl_conflicts']) for x in p['positions']))
print('Telemetry gaps:',p['telemetry_gap_events'])
print('Trades:',t['total_trades'],'Net R:',t['overall']['net_r_sum'])
print('By direction:',t['groups']['direction'])
print('By UTC hour:',t['groups']['entry_hour_utc'])
PY
```

This analysis must not be used to justify risk increases, real-time exits,
or modifying trading policies without separately tested execution-aware
walk-forward and out-of-sample evidence.
