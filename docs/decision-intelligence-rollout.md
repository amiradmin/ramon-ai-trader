# Decision Intelligence rollout — safety-first

**Status:** Implementation plan; only the standalone read-only trade audit is implemented in this branch. Other milestones below are NOT claimed complete.

## Existing components to integrate, not duplicate
- Chronos-2 / TimesFM experimental, selector and AI Engine v2/v3 shadow.
- Market-state policy and spread/ATR controls.
- SQLite decision/trade telemetry, replay, web dashboard and Docker healthchecks.
- Any existing confidence, risk and anomaly handlers must be inspected before modifying behavior.

## Work packages and acceptance gates
1. **Data contracts and safety:** verify timestamp normalization, data gaps, missing spreads, Bid/Ask and net outcome units. Fail closed on missing live data. Test against recorded MT5 data.
2. **Trade Memory (shadow):** historical net-outcome summaries and calibrated out-of-sample scoring grouped by regime, UTC time, version and realistic spread. No use of outcomes unavailable at prediction time.
3. **No-Trade candidate (shadow):** benchmark abstention against original policy on identical holdout samples; report forgone winners and prevented losers; never count trade reduction alone as improvement.
4. **TP-before-SL probabilities (shadow):** label paths from tick/quote replay, resolve same-tick/order ambiguity conservatively, calibrate probabilities and assess Brier score and net expectancy. No fabricated probability from a raw model score.
5. **Position-management challenger:** compare exit policies on the same ticks, include SL modification latency, commission and slippage; do not modify MAIN EA until reviewed.
6. **M5/M15/H1 context:** use only closed bars at decision timestamps; compare incremental net gain to M15 baseline with purged walk-forward folds.
7. **Adaptive sizing:** bound by current account/magic risk limits, broker min-lot and daily drawdown stops; no martingale/loss recovery; explicit manual approval for live enablement.
8. **Replay lab:** log measured coverage, quote age, real spread and gaps; segment out-of-sample by regime and time; report PF, drawdown, uncertainty and bootstrap intervals.
9. **Champion/challenger:** immutable model artifacts, strict promotion gates and rollback. Version every decision.
10. **Monitoring:** expose audit output in a non-executing dashboard section only after validating source units and identities.
11. **Resilience:** verify Docker restart, systemd Docker enablement, volume backup/restore, storage alarms, idempotent ingestion and order reconciliation before restart.
12. **Decision explanation:** show inputs/reasons, calibrated estimates only when genuine, policy vetoes and data freshness; avoid invented confidence percentages.

## First implemented slice
`python -m ramon.trade_audit exported_closed_trades.csv --output trade_audit.json`

Required CSV columns:
`closed_utc,net_units,regime,entry_version`

`net_units` MUST be **net of commission, fees and swap**; all rows must use a consistent monetary unit. This importer never touches the trade database or sends orders. UTC buckets are fixed UTC intervals, **not** DST-sensitive London/New York market sessions. Use an audited export, do not treat gross broker profit as net.

## Live promotion stop conditions
No live promotion until tests pass, sufficient out-of-sample tick replay confirms positive expectancy after fees, comparison to previous_bar and current production is favorable, drawdown is within agreed bounds, and a documented reversible manual gate exists. Maintain existing live caps unchanged.
