# Stop/target observations and actual execution costs

## Forward simulated plans

New candidate snapshots can have a second, independently settled SL/TP outcome.
Existing journal rows migrate to `NOT_TRACKED`; they are not retrospectively
scored. A candidate needs a BUY/SELL direction, timely aligned signal, and finite
stop and target levels already present at decision time. Missing or invalid plans
are `UNAVAILABLE`, not zero-return trades.

The entry is the next full M15 bid open, plus frozen decision spread for BUY.
SELL exits use bid OHLC plus the same frozen spread as an ask approximation.
If the next opening quote invalidates the stop/target geometry, no entry is
simulated. Freeze up to 24 exact, completed, same-source/symbol M15 candles
(six wall-clock hours). Each candle is frozen when first observed. Later database
revisions cannot change an already-processed prefix or a terminal outcome.

Stop gaps exit at the adverse opening quote. Favorable target gaps receive the
planned target, without assumed price improvement. If both stop and target are
touched within one bar after an opening quote between the levels, stop wins.
Otherwise exit at the final candle close after 24 bars. Missing/off-grid or
duplicate candles cannot be jumped over; after a two-hour receipt grace period
that window is skipped. A forming or not-yet-received bar never reaches scoring.

Simulated points and R include the assumed frozen spread, but no commission,
slippage or swap. They are neither broker P&L nor proof an unapproved order could
have executed. Each candidate is observed independently; overlapping candidates
do not constitute a portfolio backtest. The six-hour limit is a research
convention and does not change broker order management.

`candidate-worker` settles these rows through `settle_plans`, separately from the
existing one-hour price-movement observation. The forward-journal dashboard has
a **Stop and target results** table grouped by setup and decision disposition.

## Broker costs

The read-only endpoint `/api/ea/v1/research/execution` is scoped to the latest demo
EA instance, account login and symbol. It summarizes at most the latest 200 closed
trade outcomes and 200 executed OPEN commands, separately counted. Closed trade
cash charges come from the existing immutable `LearningTradeOutcome` stream.
Commission includes broker fees for compatibility with the existing EA contract;
v1.08 also sends `broker_commission` and `fee` separately. These are signed cash
flows in account currency. Existing net P&L already includes them: never subtract
them a second time or mix them with symbol points.

New closed outcomes preserve reported volume-weighted entry price. When an exact
account-scoped OPEN command links a decision, the server also derives signed
decision-to-fill movement and the decision spread. That price difference includes
pre-submission delay; it must not be called pure broker slippage. The legacy
nonnegative `slippage_points` field is retained for compatibility, but the new
panel deliberately uses the accurately labelled signed measurements.

Precise request slippage uses the EA's actual `CTrade.RequestPrice()` and broker
`CTrade.ResultPrice()` in the executed OPEN acknowledgement. It is signed:
`(fill-request)/point` for BUY and `(request-fill)/point` for SELL. Positive is
adverse; negative is favorable. Submission spread uses the bid/ask observed
immediately before submitting. Missing or zero request prices remain unavailable.
Partial/placed/reconciliation-required orders are not counted as fully executed
observations. The dashboard exposes request/fill pairs alongside sample and
missing-data counts. No cost observation creates or changes an order.

The price methods were checked against the official
[RequestPrice](https://www.mql5.com/en/docs/standardlibrary/tradeclasses/ctrade/ctraderequestprice)
and [ResultPrice](https://www.mql5.com/en/docs/standardlibrary/tradeclasses/ctrade/ctraderesultprice)
documentation. Observations are authenticated EA reports, not an independent
broker-statement reconciliation. Close-request slippage is not yet instrumented;
the precise request metric currently covers OPEN commands only.

## EA activation

`mt5/RamonCommandEA_v1.08.mq5` is the new supported source; the update/build script
now compiles that version. This workstation's v1.08 binary has been compiled and
installed next to v1.07. The old attached EA is not silently replaced or restarted.

In MT5, replace the current RamonCommandEA v1.07 on its chart with v1.08 and retain
the same local inputs, including instance ID, API URL and bearer token. Do not
attach two command EAs with the same instance simultaneously. The dashboard reports
the active version and waits for usable broker acknowledgements before showing
precise slippage. Do not send a test order just to populate the panel. Existing
EA versions can continue supplying their closed-trade commission, swap and fill
reports while awaiting the upgrade.

## Retry compatibility

Old stored outcomes retain their original supported-metric schema on retries;
newly supported metadata does not make a legacy retry conflict. Server-enriched
decision tags are consistently reapplied before duplicate comparison, including
score normalization. Conflicting P&L, times, risk or previously captured metrics
still fail with HTTP 409. No stored closed outcome is overwritten or backfilled.

## Verified rollout — 9 November 2026

The complete 419-test Django/SignalFlow suite passed on isolated PostgreSQL
17/TimescaleDB, including ambiguous bars, short ask exits, adverse gaps, invalid
entry gaps, exact 24-bar timeout, immutable prefixes/outcomes, missing candles,
signed slippage, account isolation and duplicate/conflicting outcome delivery.
Python, JavaScript, shell, Compose and migration-consistency checks passed.

The migration and local Django/candidate-worker deployment completed successfully.
Both dashboard panels were visually verified. The cost endpoint found 68 scoped
closed outcomes with commission/swap reports and 18 executed OPEN commands. None
had precise request-price observations; they correctly remain unavailable.
The currently reporting EA is still v1.07. EA v1.08 was compiled with zero errors
and zero warnings and installed alongside it; activation in MT5 is the remaining
user step. No broker order was sent to test these observations. The isolated
PostgreSQL test container was removed.
