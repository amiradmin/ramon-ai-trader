# Ramon V2: remaining seven Decision Intelligence milestones

**Status 2026-10-11: in progress, not complete.** This branch adds
**offline research scaffolding** for milestones 6 and 8. It does not enable
features in the live MT5 EA, decision API, Docker, or dashboard.

The closed-trade audit on 440 historic XAUUSD_l trades shows 239 wins,
199 losses, two flat, total **-26.306758 R**, R-unit PF **0.789839**,
and net **-878.4 account units**. Before considering any live change,
verify units, version/regime mix, and out-of-sample performance. Hourly
correlations are not validated time-window filters.

| # | Milestone | Acceptance before completion | Current status |
|---|---|---|---|
| 6 | M5/M15/H1 context | Completed point-in-time bars, gap/freshness checks, M15-only baseline versus multitimeframe candidate in purged walk-forward holdouts | Offline closed-bar extractor added; performance comparison pending |
| 7 | Adaptive sizing | Offline broker min-lot, money units, margin, account/magic exposure, daily drawdown constraints and stress tests; no martingale | Blocked: no validated risk and execution replay; **no live sizing changes** |
| 8 | Replay lab | Real Bid/Ask ticks over broad period, entry plans, costs, slippage, holdout fold PF/meanR/drawdown/bootstrap uncertainty, coverage and quote age | Offline chronological purged splitter added; labels and samples pending |
| 9 | Champion/challenger | Immutable artifacts, provenance, gated promotions with manual approval and reversible rollback | Not implemented |
| 10 | Monitoring | Research-only audit UI, no raw account IDs, source and units verified, dashboard unaffected during failures | Not implemented |
| 11 | Resilience | Restart/backup/restore and idempotence tested, disk alarms, cold-start order reconciliation | Needs separate host-side Docker/systemd acceptance |
| 12 | Decision explanation | Timestamped exact inputs and veto reasons, stale/missing-data handling; calibrated scores only when validated | Not implemented |

## Offline tools introduced

Point-in-time three-timeframe bar check (all timestamps are **UTC seconds**;
M5/M15/H1 `history_bars.time` is assumed to be candle **open UTC**):

```bash
env -u PYTHONPATH PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  uv run --no-sync pytest -q \
  tests/test_multitimeframe_context_audit.py \
  tests/test_purged_walkforward_audit.py

uv run --no-sync python -m ramon.multitimeframe_context_audit \
  --db data/ramon_history.sqlite3 \
  --symbol XAUUSD_l \
  --decision-utc 1790830800 \
  --output data/research/multitimeframe_context.json
```

This is a **single time-point diagnostic**, not a training data build;
lack of M5 or H1 data returns `ready=false`, rather than synthetic bars.

Replay fold manifest needs a vetted research CSV with three columns:
`sample_key,decision_utc,label_end_utc`. The label end is when the
outcome becomes knowable, not when a guess was made.

```bash
uv run --no-sync python -m ramon.purged_walkforward_audit \
  --samples data/research/replay_samples.csv \
  --folds 3 --embargo-seconds 3600 \
  --output data/research/purged_folds.json
```

This manifest makes **no claims of profitability**. The train set is
restricted to labels that finished strictly before the test start minus
embargo; the helper does not itself fit a model. If a fold has zero
training rows it is `ready=false`, not an invitation to skip validation.

## Gates before live trading changes

1. Required historic event/price data covers multiple conditions and versions,
   clocks are independently reconciled, failures and missingness are measured.
2. All offline tests and CI pass. The whole strategy is tested with costs,
   point-in-time data, comparison baseline and purged walk-forward holdout.
3. Multiple folds show economically significant net edge with uncertainty
   bounds, and drawdown does not violate existing constraints.
4. Explicit manual sign-off and a tested rollback exist.
5. Until all gates pass, leave MAIN order rules and risk limits unchanged.

Do not commit local research reports or account identifiers.
