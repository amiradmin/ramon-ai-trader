# Setup rule study — 2026-11-22

The breakout-only hypothesis did not hold up in the later evaluation period.
It does not justify changing demo or live settings.

## Scope and method

Using the standard combined SignalFlow policy, compare its two enabled setups
with Trend Pullback only and Breakout Retest only. Each variant is replayed
independently, so removing one setup can change later entries and position
availability. The previously inspected 2024-02-01 to 2024-04-30 period is
development data, not an independent validation. Freeze the isolated variant
with highest positive expectancy and at least ten trades, then compare it with
the unchanged combined baseline over 2025-06-01 to 2025-08-31 UTC, a later
period not previously inspected in this study. Selection and later comparison
are implemented in `scripts/research/setup_rule_study.py`. Full results and
trade records are in `setup-rule-study-2026-11-22.json`.

Data: `litefinance-mt5-live-export-20261122`, `XAUUSD_l`, M15 built from the
user's M1 export SHA-256
`c18f599a657d5dcad53bf3a9c8bcfebd09fc3a9a0055d099f33d3d2c30e1fa83`.
All M15 rows used were grid-aligned. Development has 5,931 bars including
warmup and 66 gaps greater than 15 minutes; evaluation has 6,085 including
warmup and 69 such gaps. Gaps include normal closures and possible data loss.
The importer discarded incomplete M15 groups and did not fill gaps. Server
timezone conversion is based on LiteFinance's published UTC+2 winter / UTC+3
summer rule, with transition Sundays rejected; historical switch hours are
not independently verified.

Repository HEAD at study time: `42e6ba729c2c8a74af902d1edc55835274db3f78`.
The existing container image was used; exact correspondence with that checkout
was not verified. Effective profile fingerprint:
`89eb48cc70ac89dc34cf47820b3d708ae72f9953f9d38ff6502593e7ca499dba`.
Standard threshold values include opportunity score 0.55, entry quality 0.55,
spread ceiling 80 points, maximum risk 0.5%, stop buffer 0.25 ATR, and stop
range 0.5–3.5 ATR. Dedupe was disabled for the offline replay.

## Results

| Period | Variant | Trades | Win rate | Net R | Net R / trade | Net units | Closed-equity drawdown |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Development | Combined baseline | 67 | 37.3% | -12.416 | -0.185 | -51.40 | 7.71% |
| Development | Trend only | 58 | 32.8% | -16.537 | -0.285 | -64.93 | 7.80% |
| Development | Breakout only | 11 | 54.5% | +2.059 | +0.187 | +3.56 | 0.98% |
| Evaluation | Combined baseline | 65 | 50.8% | +5.172 | +0.080 | +22.44 | 1.95% |
| Evaluation | Breakout only | 14 | 42.9% | -2.609 | -0.186 | -10.81 | 1.84% |

In the development baseline, 58 trend trades contributed -16.537 R, whereas
nine breakout trades contributed +4.121 R. In the evaluation baseline, 50
trend trades contributed +8.788 R and 15 breakout trades contributed -3.616 R.
These are descriptive subsets of the combined runs, not standalone strategy
returns. In development 19 entries in the 18:00–23:59 UTC block contributed
-10.398 R; in evaluation 17 entries in that block contributed +9.321 R. No
time filter was selected from these observations.

## Assumptions and limitations

Simulated balance 1,000 account units; point/tick size 0.01, tick value 1 per
lot, lot min/step 0.01, max 100, stop level 0. A stored `XAUUSD_l` heartbeat
reported these contract values and point; it does not verify historical specs,
account currency or commission. Fixed spread 42 points, slippage 5 points per
side and round-trip commission 5 points are illustrative assumptions. Raw M1
spread medians by broker date were 22 points in development and 20 points in
evaluation (95th percentiles 40 and 22); the replay does not use each actual
minute's spread. The fixed 42-point assumption is higher than those medians,
but is not a validated fill cost and can change which trades pass filters.

Next-open entry, one position at a time, ask-side short exits, adverse stop
gaps, stop-first ordering and 24-bar time exit are used. The time exit is a
research convention. Drawdown is based on closed equity, not intratrade risk.
No M5/H1 context, historical account state, swap, tick path, variable spread,
or actual commission is modeled. This is an OHLC approximation, not full
worker/live parity. The samples are small and different market regimes can
reverse the apparent ranking. No unchanged-price forecast was assessed: this
experiment tests trading rules rather than forecasting. No settings changed.
