# H1/H4 paper candidate — initial validation, November 10, 2026

Frozen version: `h1-h4-breakout-v1`. Requested as a third separate container;
implemented as research only. The candidate policy was documented before replay
results were inspected; no optimization or broker activation was performed.

Coverage: 2026-10-06T08:00:00+00:00 to 2026-11-10T07:00:00+00:00.
Source `ramon-command-ea-1`, symbol `XAUUSD_o`, demo broker feed. 575 valid H1
candles, zero invalid/off-grid exclusions, 25 timestamp gaps, 125 complete derived
UTC H4 candles. No gaps were filled. Missing outcome bars are skipped.

Base costs: spread 22 points, slippage 5 points each side, round-trip commission
5 points, overnight debit assumption 50 points per rollover (Wednesday triple,
22:00 UTC). Stress doubles spread, slippage and overnight debit. Contract and
costs are current fixed assumptions, not historical executable quotes or broker
swap terms. Paper/replay entry occurs one hour after the signal H1 closes, at
the next full H1 after recording. No position sizing or account return is claimed.

Both scenarios produced **zero fully scored trades**. Each rejected 231 decision
windows for H4 warmup, 194 for recent H1 gaps, 48 for stale derived H4 context,
24 for no breakout, 9 for trend disagreement, and skipped one attempted trade
for invalid/missing outcome data. Zero net R and zero closed cumulative-R drawdown
are empty-sample accounting values, not evidence of capital preservation or edge.
Expectancy and win rate are undefined. No-position baseline is zero trading P&L.
This cannot establish an advantage over main Ramon or the scalper.

Artifacts: `research/trend/replay-2026-11-10.json` and
`research/trend/snapshot-2026-11-10.json`. Input SHA256:
`52e17d372264dad2bbb72e46b47e9d4668376f7de0d2bfd4b0c5092ce6766e50`. Policy source SHA256:
`063b11e652fd7d69f95b7a92fda46d6ab791cd21122a523c294d59e7170079d6`. The replay includes persisted main thresholds.
The container lacks Git metadata; source hash identifies the candidate code and
this working tree descends from commit 63c6abd. Historical receipt availability
was not reconstructed. This is descriptive development evidence, not out-of-sample.

43 PostgreSQL tests passed across the candidate, scalper engine, scalper journal
and scalper strategy. Tests cover both directions, aggregation, causal receipt
cutoffs, costs, adverse stop gaps, stop-first ambiguity, ask-side shorts, paper
entry geometry, funding, frozen outcomes, input/terminal immutability, and the
absence of broker commands. Migration parity, Python compilation, dashboard
JavaScript syntax and Compose validation passed.

Deployment: `ramon-trend-worker` and Django healthy; worker waiting for the next
fresh H1 evaluation window. Existing scalper remains automatic with 0.150% risk.
No trend broker commands exist and command gates are forced off in the trend
container. Forward data starts at deployment, never from historical backfill.
Continue paper collection without changing the frozen candidate based on these
results. Aligned exposure/cost/sample comparisons and prospective observations
are required before considering any broker activation.
