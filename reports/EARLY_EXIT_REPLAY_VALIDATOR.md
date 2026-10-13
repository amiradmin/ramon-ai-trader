# Early Exit Replay Validator

This tool only checks time-aligned OHLC data coverage and whether an actual entry
fill and model-planned stop/target distances exist for candidate autonomous
early-adverse exits. It is NOT an executable replay. The derived price levels
are **planned model levels**, not verified broker SL/TP.

Run after merging and checking local tests:

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run --with pytest python -m pytest -q tests/test_early_exit_replay_validator.py
uv run python -m ramon.early_exit_replay_validator --db data/ramon_history.sqlite3 --lookahead-minutes 60 > /tmp/ramon_early_exit_validator.json
```

The output reports M1/M5 continuity after the historical close. `counterfactual_status`
remains `NOT_EVALUATED`; `hypothetical_net_units` remains null.

For evaluating apparently missed BUY opportunities in an upward move, the next
research step is a time-aligned **WAIT opportunity audit** rather than concluding
from the later chart trend: compare each contemporary WAIT decision (including
market hazard and signal strength) to a future price path, require sufficient
M1/tick coverage, and report independent signal bars / UTC days. Shadow
recommendations must not be mistaken for executable policy.

No mutation of source data, broker positions or live EA behavior is performed.
