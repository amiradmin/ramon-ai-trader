# Cost-aware selector evaluation — 2026-10-03

Final protocol: 24 completed-bar features, dual action-value ridge models, five expanding outer folds, chronological inner selection, H4, training stride 16, evaluation stride 4, assumed spread 42 points and extra cost 0.1R. Data, settings, candidate metrics, label bounds and implementation hashes are preserved in `trade-selector-evaluation.json`.

## Decision

The final selector makes only **6 held-out trades**, all BUY trades in fold 1. Pooled PF is 1.728, MeanR 0.172, net 1.033R and closed-trade drawdown 1.003R. This tiny positive sample does not establish an edge. Folds 2–5 select no qualifying inner candidate and use WAIT throughout. The broker-validation gate fails because trade count and stability are insufficient. Broker confirmation, realtime Shadow and live integration were not launched.

The selected first-fold inner candidate itself is marginal: 36 validation trades, PF 1.00599 and MeanR 0.001688. The outer results did not influence selection. Research representations were iterated on this already-used archive, so these are algorithmic OOS results, not a pristine project-wide holdout.

## Held-out fold evidence

| Fold | Training samples | Inner selected penalty / threshold | Outer trades | PF | MeanR | DD (R) |
|---|---:|---|---:|---:|---:|---:|
| 1 | 5497 | 1.0 / 0.0R | 6 | 1.7284 | 0.1722 | 1.0030 |
| 2 | 10179 | none → WAIT | 0 | undefined | undefined | 0.0000 |
| 3 | 14902 | none → WAIT | 0 | undefined | undefined | 0.0000 |
| 4 | 19625 | none → WAIT | 0 | undefined | undefined | 0.0000 |
| 5 | 24341 | none → WAIT | 0 | undefined | undefined | 0.0000 |

## Fixed-exit reference policies

| Policy | Trades | PF | MeanR | NetR | Closed DD (R) |
|---|---:|---:|---:|---:|---:|
| SELECTOR | 6 | 1.7284 | 0.1722 | 1.0330 | 1.0030 |
| always_buy | 57110 | 0.4361 | -0.2829 | -16158.5247 | 16161.9936 |
| always_sell | 57003 | 0.4347 | -0.2846 | -16224.0326 | 16232.1622 |
| previous_bar | 56827 | 0.4348 | -0.2878 | -16355.4167 | 16359.7930 |
| contrarian_previous_bar | 56765 | 0.4392 | -0.2784 | -15802.6056 | 15805.5912 |
| momentum_4bar | 56915 | 0.4293 | -0.2927 | -16656.4814 | 16666.3773 |
| contrarian_momentum_4bar | 57051 | 0.4374 | -0.2795 | -15944.3543 | 15945.5673 |

All reference policies have negative cost-adjusted MeanR. They use the same fixed labels/exits and eligible grid, not the old forecast lab entry-strength filters. The selector trades a different, tiny subset; its PF cannot be treated as robust outperformance merely because the reference policies lose.

## Artifacts and checks

All 15 fitted inner BUY/SELL pairs (three penalties per fold) are saved as local JSON, including rejected pairs. The selected fold-1 refit is saved separately. No unknown third-party models were loaded. Final source hashes and training label cutoffs were verified against the completed report.

40 focused tests passed. Coverage includes hypothetical outcome parity with the existing simulator for both directions and all exit cases; input database preservation; non-overlapping positions; outer and inner label purging; and invariance of first-fold candidate selection to changed future prices. The final protocol runs on one Python CPU process.

Neither the installed EA nor the dashboard was updated; this research has no live server hook or order route. A dashboard update becomes relevant only after an advisor passes independent broker validation and is intentionally connected to realtime Shadow. See `trade-selector-lab.md` for exact pull/test/run commands.
