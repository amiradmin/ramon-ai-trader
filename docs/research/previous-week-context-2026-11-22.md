# Previous-week context — 2026-11-22

This is an exploratory hypothesis for the expanded demo candidate selector,
not a validated profit improvement. It is disabled by default and does not
change the standard, live or independent-worker decision paths. See the
professional demo playbook for the UTC week definition, coverage rules,
ranking adjustment and activation switch.

## Comparison

Using the current expanded demo detector and persisted thresholds, compare
the original selector against the same selector plus a fixed +0.05/-0.05
ranking adjustment for alignment/opposition with the previous completed UTC
week's direction. Both use the same signal data, exposure and costs. The
2025-06-01 through 2025-08-31 UTC window was inspected in prior studies, so
this is descriptive, not untouched out-of-sample evidence. The compared
policies use the same current detectors; this is not historical binary parity.

| Mode | Closed trades | Net R | Net R / trade | Win rate | Net units | Max closed-equity drawdown |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Existing demo selector | 246 | -37.146 | -0.1510 | 39.84% | -124.59 | 14.83% |
| Previous-week adjustment | 246 | -33.980 | -0.1381 | 40.24% | -113.47 | 13.83% |

Weekly context was AVAILABLE for 4,345 decisions and INCOMPLETE_HISTORY for
827. Both H1 and M5 context were missing throughout, so the replay cannot
establish full demo parity. Even the weekly version loses money; no setting
should be activated based solely on this result.

## Reproducibility and execution

- Script: `scripts/research/previous_week_study.py`; complete trade records,
  assumptions, effective profiles and input fingerprint are in the adjacent
  `previous-week-study-2026-11-22.json`.
- Source `litefinance-mt5-live-export-20261122`, `XAUUSD_l`, completed M15,
  derived from M1 CSV SHA-256
  `c18f599a657d5dcad53bf3a9c8bcfebd09fc3a9a0055d099f33d3d2c30e1fa83`.
  UTC+2/+3 conversion is based on the broker's published rule; transition
  Sundays are rejected. Historical offset changes have not been independently
  verified. The replay contains 6,085 bars including warmup and 69 intervals
  over 15 minutes; these include closures and potentially missing data. Weekly
  context was supplied with prior history and incomplete weeks remain neutral.
- Effective standard/demo profile fingerprint
  `89eb48cc70ac89dc34cf47820b3d708ae72f9953f9d38ff6502593e7ca499dba`.
  Expanded demo thresholds: minimum opportunity score 0.42, minimum entry
  quality 0.42, maximum spread 100 points, per-trade risk ceiling 0.5%, stop
  buffer 0.2 ATR, stop range 0.6–3 ATR; strict structure and short spread
  adjustment enabled. Dedupe is disabled for replay. The repository HEAD was
  `42e6ba729c2c8a74af902d1edc55835274db3f78` with local research changes;
  tests ran with a temporary code copy and the existing container dependencies.
- Fixed spread 42 points, slippage 5 points on each side and round-trip
  commission 5 points. These costs are illustrative, not verified fills.
  Starting balance 1,000, point/tick size 0.01, tick value 1, lot minimum and
  step 0.01, max 100, zero stop level. Recorded current broker metadata matches
  these contract values but does not verify historical specs or commission.
- Next-open entry, one position at a time, conservative stop-first, adverse
  stop gaps, ask-side short exits and 24-bar research time exit. Drawdown is
  closed-equity only. No H1/M5, historical account state, variable spread,
  tick path or swap. Historical receipt times were unavailable; backfilled
  records do not establish what was known at the time. Forward decisions do
  filter by receipt time. No forecasts were assessed in this trading-rule test.
