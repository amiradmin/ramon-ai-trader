# LONA research comparison — 9 November 2026

Two fixed-rule research strategies were created with the connected LONA plugin
and tested on the already-uploaded **XAUUSD_o M15 LiteFinance MT5 History** dataset
(`5971a9c3-0671-4acb-b785-19e849c4901b`). Both complete sources passed LONA's dry
run. This is an exploratory comparator experiment, not a test of the complete
deployed Ramon policy and not a trading-settings change.

## Reproducible inputs

- Ramon base commit: `f985debe4f21201af6ea8d28cd4cb9f0e010489d`.
- Baseline source: `research/lona/trend_baseline.py`, SHA256
  `f5b1c36d0183350888abe8a67a6652bdb4cbd5702dc584e5f3889d6d0f22f7d7`.
- Challenger source: `research/lona/pullback_challenger.py`, SHA256
  `3b92c548745b49fde998fcf25a6a3ac4477db42833aa34c89bbefa7a17d8f95c`.
- Source history metadata: 13 June 2022 through 4 September 2026; M15.
  The connector exposes dataset identity but not a content checksum. Candle
  quality and the uploaded broker timestamp conversion were not independently
  audited in this run.
- Both runs: $100,000 starting cash, 10% target portfolio exposure, leverage 1,
  commission rate `0.0001`, `buy_on_close=false`, fixed rules and no optimization.
- Orders execute next open. ATR exit conditions are evaluated at candle closes;
  these are not broker intrabar stop/target orders. No explicit spread, slippage,
  swap or MT5 contract/lot mechanics were modeled.
- EMA/ATR warmup occurs inside each selected period. Each period restarts with
  fresh cash and indicators; this is not a continuous compounded equity curve.

## Results

Provider aggregate statistics, including modeled commission, are authoritative.
Returns are percentage points of the simulated portfolio, not summed R.

| Requested period (UTC, inclusive dates) | Strategy | Trades reported | Return | Max drawdown | Win rate | Open positions at end |
|---|---|---:|---:|---:|---:|---:|
| 1 Jan–30 Jun 2025 | EMA trend baseline | 175 | +0.30% | 0.61% | 45.14% | 0 |
| 1 Jan–30 Jun 2025 | EMA pullback challenger | 325 | +0.09% | 0.76% | 42.46% | 0 |
| 1 Jul 2025–1 Jan 2026 | EMA trend baseline | 188 | +0.22% | 0.35% | 43.09% | 0 |
| 1 Jul 2025–1 Jan 2026 | EMA pullback challenger | 340 | +0.65% | 0.67% | 43.24% | 1 |

Exact provider report IDs, settings and aggregate metrics are saved in
[`research/lona/results-2026-11-09.json`](../../research/lona/results-2026-11-09.json).
The two periods do not share a requested calendar day. These are descriptive
chronological checks of frozen rules, not evidence of untouched data: this
uploaded history already existed in the account before this experiment.

## Interpretation

The pullback variant found more entries, but it underperformed the simpler trend
baseline in the first period and had greater drawdown in both periods. The later
period's higher return is insufficient to establish a consistent improvement.
One challenger position remains open at that boundary, so final portfolio value
includes its mark-to-market effect. Do not equate the provider's `realized_pnl`
field with fully closed-only P&L when `open_trades` is nonzero.

These small returns are especially sensitive to omitted spread and slippage.
No strategy is promoted to trading. A useful next experiment is a full SignalFlow
comparison on validated broker candles with account-specific costs, using the
existing `evaluate_demo_playbook` replay and a frozen later evaluation period.
The LONA results cannot be directly ranked against the earlier Ramon R report
because the policies, sizing and execution models differ.

## Project verification

The complete 401-test Django/SignalFlow suite passed on an isolated disposable
PostgreSQL 17/Timescale database. Runtime Django also reports the PostgreSQL
backend. The calendar importer and GET endpoint are covered for missing, corrupt,
stale and incomplete snapshots, invalid values, explicit offsets and naive times.
The installed skill passed its validator; dashboard JavaScript passed Node's
syntax check. The local event panel was deployed and visually verified with 33
imported events. Its provider timezone remains unverified and it is not an
automatic event subscription.
