# Opportunity threshold study — 2026-11-22

Exploratory research, not a recommendation to trade or a live-policy change. The
development period had already been inspected using a different simulator. The
2025 evaluation period was not used to select the candidate in this study.

## Data and reproducibility

- LiteFinance `XAUUSD_l` M1 export SHA-256:
  `c18f599a657d5dcad53bf3a9c8bcfebd09fc3a9a0055d099f33d3d2c30e1fa83`.
- Source `litefinance-mt5-live-export-20261122`, aggregated M15. The importer
  accepted 1,000,000 M1 bars, created 65,424 complete M15 bars, and excluded
  1,414 incomplete M15 groups. No off-grid bars were included. Broker server
  conversion uses GMT+2/GMT+3 and excludes transition Sundays; exact historical
  transition hours are unverified. Gaps include closures and possible missing data.
- Repository HEAD `42e6ba729c2c8a74af902d1edc55835274db3f78`; the study ran
  against the existing Django container image, whose correspondence to that
  checkout was not verified. Effective profile fingerprint:
  `89eb48cc70ac89dc34cf47820b3d708ae72f9953f9d38ff6502593e7ca499dba`.
- Script: `scripts/research/threshold_study.py`. It reads the database and
  applies changes to in-memory research pipelines only. It compares standard
  single-selection policy variants, not independent strategy-worker results.
  Standard effective thresholds: opportunity 0.55, entry quality 0.55,
  maximum spread 80 points, maximum risk 0.5%, stop buffer 0.25 ATR, stop
  range 0.5–3.5 ATR, strict structural stops and short spread adjustment enabled.

## Experiment

Development: 2024-02-01 through 2024-04-30 UTC, 5,931 bars including warmup,
66 intervals longer than 15 minutes. Test three minimum opportunity scores:
0.55 (baseline), 0.65, and 0.75. Freeze the eligible score with highest net R
per trade, requiring at least ten trades. This selected **0.75**, despite all
three having negative expectancy.

Evaluation: 2025-02-01 through 2025-04-30 UTC, 5,830 bars including warmup,
65 intervals longer than 15 minutes. Compare the frozen 0.75 against baseline.
Gaps include warmup; they are not automatically proof of missing trading data.

| Period | Score | Trades | Net R | R per trade | Win rate | Net account units | Max closed-equity drawdown |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Development | 0.55 | 67 | -12.416 | -0.185 | 37.3% | -51.40 | 7.71% |
| Development | 0.65 | 65 | -10.314 | -0.159 | 38.5% | -44.08 | 6.97% |
| Development | 0.75 | 50 | -7.145 | -0.143 | 38.0% | -27.84 | 5.19% |
| Evaluation | 0.55 | 40 | -10.023 | -0.251 | 35.0% | -38.57 | 5.25% |
| Evaluation | 0.75 | 25 | -5.690 | -0.228 | 36.0% | -26.74 | 3.57% |

## Execution and limitations

Starting balance 1,000; point/tick size 0.01, tick value 1 per lot, lot min and
step 0.01, max 100, stop level assumed zero. Fixed spread 42 points, slippage
5 points on each side, round-trip commission 5 points. These are illustrative,
not verified broker costs or contract specifications. The 42-point spread was
chosen after inspecting the 2026 export, not measured at each trade. The replay
uses next-open entry, one position at a time, ask-side short exits, adverse stop
gaps, stop-first intrabar ordering, and a 24-bar maximum holding period. The
last is a research convention, not the live exit policy. Drawdown uses closed
equity only and can understate intratrade drawdown. Historical M5/H1 context,
received-at timestamps, swaps, variable spreads, broker fills and account state
are absent. This is not full live-worker parity. No price-forecast comparison
was performed because this study tests a trading threshold, not a forecast.

Both evaluation results lose money. The selected threshold filtered trades and
reduced the observed loss, but 25 evaluation trades and one later window are
insufficient evidence of a durable edge. No production setting was changed.
