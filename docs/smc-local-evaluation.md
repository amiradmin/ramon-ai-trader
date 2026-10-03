# SMC_LOCAL evaluation — 2026-10-03

The fixed five-fold expanding-window experiment completed on the external M15 archive. No parameter search or live change was performed. Input fingerprint and implementation hashes are preserved in `smc-local-evaluation.json`. Models remain local JSON research artifacts under `data/smc_local_lab/`.

## Finding

SMC_LOCAL produced **one trade across all five folds**, in the final fold. It lost 1.1R including the additional cost (PF 0, MeanR -1.1R, closed-trade DD 1.1R). The first four folds had no trades and therefore undefined PF/MeanR. One losing trade is insufficient evidence to assess trade performance or stability. The research shadow gate failed; no shadow database or votes were created. This rejects this fixed initial ridge version for promotion, not every possible SMC model.

## Same-condition comparison

Horizon 4, evaluation stride 4, assumed fallback spread 42 points and additional 0.1R round-trip cost. Training stride 16, ridge penalty 1.0, training-only normalization and residual uncertainty. All five evaluation blocks use the timestamp gap guard and the same execution settings for all advisors.

| Model | Trades | PF | MeanR | NetR | Closed-trade DD (R) |
|---|---:|---:|---:|---:|---:|
| SMC_LOCAL | 1 | 0.0000 | -1.1000 | -1.1000 | 1.1000 |
| previous_bar | 37769 | 0.4432 | -0.2832 | -10697.9259 | 10706.7634 |
| momentum_4bar | 25734 | 0.4580 | -0.2788 | -7173.6787 | 7178.7716 |
| contrarian_previous_bar | 37602 | 0.4434 | -0.2761 | -10380.5993 | 10381.5935 |
| contrarian_momentum_4bar | 24748 | 0.4722 | -0.2573 | -6366.9623 | 6367.4114 |

## Fold evidence

| Fold | Training samples | First evaluation UTC | SMC evaluated decisions | SMC trades | Raw direction accuracy |
|---|---:|---|---:|---:|---:|
| 1 | 5497 | 2008-12-18T12:30:00+00:00 | 18637 | 0 | 50.21% |
| 2 | 10179 | 2012-07-10T16:15:00+00:00 | 18891 | 0 | 50.05% |
| 3 | 14902 | 2015-11-24T07:30:00+00:00 | 18897 | 0 | 51.28% |
| 4 | 19625 | 2019-04-09T00:15:00+00:00 | 18896 | 0 | 50.90% |
| 5 | 24341 | 2022-08-15T16:00:00+00:00 | 18894 | 1 | 50.54% |

Aggregate SMC decision reasons:

- `insufficient_model_edge`: 79,386
- `late_entry_extension`: 9,288
- `trend_conflict`: 5,527
- `insufficient_model_strength`: 13
- `forecast_up`: 1

Directional diagnostics use evaluated decisions with a valid forecast, excluding flat forecast/actual moves from accuracy. Baseline diagnostic streams skip signals while a position is open; their raw accuracy sample sets are not identical to an all-WAIT advisor. Promotion relies on trade metrics and fold stability, not directional accuracy.

## Data sufficiency and scope

The archive contains 494,235 bars with 8,520 timestamp discontinuities and zero known spreads. There are 464,658 contiguous four-bar outcome intervals after initial 256-bar warmup; these overlapping windows are not independent samples. The actual first fold trains on 5,497 valid stride-16 samples and later folds expand on matured historical labels. This is adequate for an initial small linear model experiment, not a guarantee of edge. Sparse individual regimes must be assessed separately.

Broker history has 10,525 M15 bars, 10,507 known spreads and 9,817 contiguous H4 intervals. It was not used for training or evaluation in this experiment. Since the external candidate did not pass, broker confirmation and realtime shadow integration were not launched.

## Validation and disposition

27 focused tests passed, including purged training label bounds, price anchoring, frozen training scaling, first-fold invariance to future price edits, input database preservation, five-fold baseline comparison and shadow-output isolation.

Keep the fixed initial model as a reproducible failed research reference. Do not weaken live entry filters to create trades, treat inactivity as profitability, or promote it into Shadow/live trading. Any next model family needs its own predeclared train-only selection protocol and independent broker validation. See `smc-local-lab.md` for exact pull, test and run commands.
