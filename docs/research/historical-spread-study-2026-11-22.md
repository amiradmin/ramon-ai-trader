# Historical spread replay — 2026-11-22

Descriptive comparison of fixed-spread and historical M15 opening-spread
research replay. Both periods were inspected in earlier research, so neither
is an untouched evaluation. No policy or live execution settings were changed.

The optional `bar_open` mode reads `spread_points` from the entry candle's
opening minute at the next-open decision. For short exits it uses the opening
spread of each inspected M15 bar. It holds that opening spread constant across
that bar: the M1 spread is not a tick-by-tick ask path, and cannot establish
the actual spread at an intrabar stop or target. Missing, negative or nonfinite
opening spreads fail closed. Fixed mode remains the default.

## Data and assumptions

Source `litefinance-mt5-live-export-20261122`, `XAUUSD_l`, M15 aggregated from
the user's M1 file, SHA-256
`c18f599a657d5dcad53bf3a9c8bcfebd09fc3a9a0055d099f33d3d2c30e1fa83`.
The study checks the checksum and matches every M15 bar's open to its M1 open;
all used M15 bars are aligned. Server-time conversion follows the provisional
LiteFinance UTC+2/UTC+3 rule; transition Sundays are rejected. The development
window has 5,931 bars including warmup and 66 intervals over 15 minutes; the
comparison window has 6,085 and 69. These gaps include closures and possible
missing data. Incomplete M15 groups were excluded rather than filled.

Standard combined policy, one position at a time, next-open entry, 24-bar
maximum holding period, conservative stop-first, ask-side short exit, adverse
stop gaps. Fixed baseline uses 42 points; historical mode uses the CSV's
bar-opening spread. Both assume 5 points slippage per side and 5 points
round-trip commission, which are unverified. Starting balance 1,000 account
units; point/tick size 0.01, tick value per lot 1, min/step 0.01, max 100,
zero stop level. A recorded heartbeat matches these contract values; historical
specification changes and commission remain unverified. Drawdown is based on
closed equity, not intratrade equity. No swap, M5/H1 context, tick path or
historical account state is simulated. This is not full live-worker parity.

Repository HEAD `42e6ba729c2c8a74af902d1edc55835274db3f78` with the modified
research replay loaded separately from `/tmp` in the running container. Exact
correspondence of the container image with the checkout was not established.
The effective profile fingerprint is
`89eb48cc70ac89dc34cf47820b3d708ae72f9953f9d38ff6502593e7ca499dba`;
the full profiles and trade details are recorded in the adjacent JSON result.

## Results

| Period | Spread mode | Trades | Net R | R per trade | Win rate | Net units | Closed-equity drawdown |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Feb–Apr 2024 | Fixed 42 points | 67 | -12.416 | -0.185 | 37.3% | -51.40 | 7.71% |
| Feb–Apr 2024 | Historical bar open | 107 | -21.179 | -0.198 | 37.4% | -91.32 | 12.04% |
| Jun–Aug 2025 | Fixed 42 points | 65 | +5.172 | +0.080 | 50.8% | +22.44 | 1.95% |
| Jun–Aug 2025 | Historical bar open | 74 | +6.935 | +0.094 | 51.4% | +28.65 | 2.05% |

Changing the spread changes entry eligibility, sizing, and short exit
approximations, so the comparison is between different trade sequences. It
does not isolate cost savings on the same trades and does not establish a
profitable edge. More independent periods and verified costs are needed.

Reproduce using `scripts/research/historical_spread_study.py` inside a Django
environment with the source loaded and `RAMON_SPREAD_CSV` set to the exact
export. `RAMON_REPLAY_MODULE` can point to a separate updated replay file for
an isolated run; without it the installed replay module is used.
