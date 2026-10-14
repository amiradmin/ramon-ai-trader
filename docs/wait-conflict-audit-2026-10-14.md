# WAIT conflict research — 2026-10-14

Research-only. Do not alter live trading rules or retrain on opposite-direction snapshots.

## Observations

- 141 learnable trades have an executed direction different from their immutable snapshot direction.
- Fifteen recently sampled dashboard WAIT conflicts have entry delays of 4–17 seconds; all are within the existing 90-second temporal association window.
- This timing does not establish whether an override was authorized, or whether the linked snapshot was the exact entry decision.
- Keep `WAIT_CONFLICT`, `DIRECTION_OVERRIDE`, and `NO_SNAPSHOT_DIRECTION` as distinct observational categories; record source, decision ID, model/bundle version, quote time, entry time, and net R.

## Validation before live changes

1. Verify exact decision ID linkage and origin of final WAIT, including selector, range and reversal paths.
2. Analyze chronology without look-ahead. Compare paper-only counterfactuals using future OHLC and actual costs, with slippage uncertainty.
3. Hold out unseen days and track sample counts and confidence intervals; do not promote if insufficient independent data.
4. Do not override manual actions or modify EA/MT5 permissions as part of research.

## Current constraints

No live-blocking rule, schema migration, model promotion or risk-policy change is authorized by this document.
