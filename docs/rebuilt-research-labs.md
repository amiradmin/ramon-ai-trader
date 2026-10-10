# Rebuilt research labs — 2026-10-10

These three files are **new reconstructions from function signatures and recovered
research goals**, not exact original source recovered from the lost CPython 3.13
bytecode. The original `.pyc` and disassembly remain in the owner's
`~/ramon-recovery/bytecode` and `~/ramon-recovery/disassembly` folders;
they have NOT been committed to GitHub.

## Modules
- `moving_average_lab.py`: causal M15 EMA/SMA/ATR features, future-label
  dataset, purged forward majority baseline. Implements `features`, `dataset`,
  `evaluate`, `main`. Supports recovered `foundation_benchmark.prepare`
  imports (`BASE` and `features`).
- `candle_direction_lab.py`: cost-aware three-class future labels, closed
  candle features, split purge, probability temperature and a
  **decision-count-only** replay. No learned XGBoost predictor is claimed.
- `candle_forward_recorder.py`: explicit UTC, immutable prediction snapshots
  in idempotent SQLite, pending labels reported as unresolved. Does not
  synthesize outcomes or interact with MT5.

## Limitations and safety
- No training of XGBoost, no real-tick bid/ask PnL simulator, no classifier
  deployment, no automated model promotion and no terminal hook in this PR.
- Recorder is an offline library: it does not run a polling daemon or ingest
  broker data on its own. Never point it at a production queue expecting the
  old recorder protocol.
- No changes to the EA, live decision HTTP endpoint, risk caps or Docker
  startup services. V3 remains shadow.
- Formulae and feature names were reconstructed; historical benchmark results
  cannot be treated as reproduced until compared against saved artifacts.
- `foundation_forward.py` is preserved from the recovered runtime; its
  dependencies must be validated independently.

## Quick checks
```bash
git fetch origin
git switch research/rebuild-candle-labs-v3
PYTHONPATH=src uv run --extra dev pytest -q tests/test_rebuilt_candle_labs.py tests/test_foundation_benchmark.py
PYTHONPATH=src uv run python -m ramon.moving_average_lab --help
```
Run the tests before launching any experiment. The new tests were authored
and committed but cannot be claimed as executed on the user's terminal.
