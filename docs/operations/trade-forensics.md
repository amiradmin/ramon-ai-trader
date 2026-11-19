# Trade Forensic Analyzer (read-only)

Open `/api/ea/v1/learning/forensics` or click **Open trade forensics** in
`/api/ea/v1/learning/dashboard`. It uses existing PostgreSQL
`LearningTradeOutcome`, `EACommand`, and `DecisionEvaluation` records.
No database migration, model training, new MT5 command, or strategy-policy
change is required.

The JSON API is `GET /api/ea/v1/learning/forensics/data`; supported query
parameters: `start`, `end` (UTC ISO 8601), `symbol`, `account_login`,
and `limit` (1–500, default 250). The default interval is seven days, with
a maximum 90-day window. Returned results are the **most recent** matching
closed trades, not the entire history when truncated. Use a narrow scope and
check `sample.truncated` before interpreting aggregate statistics.
The HTML date fields include both selected UTC days.

## Interpreting evidence

The outcome's realized broker P/L is the source of net P/L; ensure the
upstream ingestion includes all relevant costs. A trade is
`VERIFIED_EVALUATION` only when its outcome references an exact persisted
OPEN command with matching account, EA instance, symbol and decision, and that
command points to an immutable matching evaluation. `LINKED_COMMAND_ONLY`
means the execution link exists but no verified evaluation does. `UNLINKED`
means attribution cannot be proven: the dashboard **never guesses** based
on closeness in time, matching direction or repeated decision keys.

Regime and setup values may be present as historical outcome tags even
when decision linkage is missing; numeric regime diagnostics (ADX, etc.)
are only shown when sourced from the verified evaluation. Null metrics
mean unavailable, not zero. Evidence only describes observations and cannot
by itself establish whether a loss came from market regime, entry timing,
stop design or broker execution. Use follow-up experiments on chronological
out-of-sample datasets, and compare the same trades between strategies.

No automatic entry restrictions have been introduced. This module has no
write endpoints and does not change MT5 order permissions or pipeline training.

## SELL strategy-research extension

The forensic API additionally returns `sell_research` and the same dashboard
renders evidence-backed comparisons of winning/losing SELL trades. A fixed set
of candidate *filters* replays **historically executed SELL outcomes only**:

1. Baseline: all verified SELL outcomes.
2. Bearish regime: retain trades whose recorded evaluated regime was BEARISH.
3. Bearish regime and BEAR EMA bias.
4. Same plus recorded entry-time chase <= 0.35 ATR.

The hypotheses are predeclared in `ea_bridge/forensic_research.py`; no optimizer
searches over the test partition. The retained-outcome comparison uses the
earlier 70% and later 30% by `closed_at`, counts skipped observations, and
reports observed realized account-currency net P/L only for retained fills.
This is **not** an executable strategy backtest, as hypothetical skipped
positions, replacement fills, overlapping trades, transaction-cost changes
and alternative exits are not simulated. A later formal backtest must replay
closed market candles with an execution model, spread/slippage, fees, risk
constraints, and time-purged out-of-sample evaluation.

Flags such as `SELL_AGAINST_BULLISH_REGIME`,
`SELL_AGAINST_BULLISH_EMA`, or `EXTENDED_ENTRY_OVER_035_ATR` are conditional
research descriptors, **not** verified causes of losses. Values absent from
the immutable evaluation are unavailable, not inferred. A small or truncated
sample will explicitly report `research_ready=false`. The active trading
engine remains untouched; no auto-filters, position restrictions, or strategy
parameter changes are applied.
