# SignalFlow

A small, readable four-stage decision pipeline for XAUUSD. It **evaluates snapshots only**:
it never places orders (`execution_enabled` is always `False`) and never retunes itself.

```
market snapshot ──► opportunities ──► entry_timing ──► risk_management ──► result
                    what to trade     when to enter    how much / stop
                          (each stage sees the earlier verdicts, none can overturn them)

closed trade ─────► learning ──► validated, de-duplicated outcome ──► advisory statistics
```

## Layout

| File | Responsibility |
| --- | --- |
| `pipeline.py` | `SignalFlowPipeline.run(market, outcome=None)` – orchestration only |
| `opportunities.py` | Regime (bullish / bearish / ranging / uncertain) + trend-following pullback setups, scored and explained |
| `entry_timing.py` | Is a *quote-level* entry acceptable now? freshness, invalidation, chasing, spread |
| `risk_management.py` | Structural stop placement and position size; account guards |
| `learning.py` | Outcome validation, optional storage, leak-safe statistics, advisory findings |
| `common.py` | Shared status vocabulary and number/timestamp parsing |
| `forecasting/` | Optional ARIMA / SARIMA evidence (veto only, opt-in, cached per bar) |
| `tests/` | One test module per stage plus `test_pipeline.py` and `test_common.py` |

Import from the package: `from ramon_pipeline.signalflow import SignalFlowPipeline, OpportunityDetector, ...`.
The older shims `ramon_pipeline.arima` / `ramon_pipeline.sarima` and the Redis stream code in
`ramon_pipeline/pipeline.py` are unrelated to this package and unchanged.

## Result contract (every stage)

Every stage result contains `approved` (bool) and `reason` (stable code), plus:

* `status`: `ACCEPTED` | `REJECTED` (invalid/unsuitable, waiting won't help) | `DEFERRED` (not ready, retry later)
* `explanation`: one human-readable sentence
* `checks`: ordered list of `{name, passed, detail}` (stages 1-3)

`SignalFlowPipeline.run` returns `action` (`BUY`/`SELL`/`HOLD`), `approved`, `rejected_by`
(first stage that said no, else `None`), `reason`, `stages{...}` and `contract_version`.

## Market snapshot

| Field | Used by | Notes |
| --- | --- | --- |
| `bars` | opportunities | Completed candles, oldest first: `time`, `high`, `low`, `close`. ≥100 bars. Presence selects the structural path |
| `symbol`, `timeframe` | all | Symbol must start with `XAUUSD` (configurable). Default timeframe `M15` |
| `bid`, `ask`, `point` | entry_timing, risk | Supply all three or none. Enables invalidation / chase / relative-spread checks and the structural stop |
| `spread_points`, `entry_quality` | entry_timing | Used when no quote is supplied (legacy flow) |
| `account_balance`, `risk_percent`, `point_value_per_lot`, `volume_step`, `min_volume`, `max_volume` | risk | Same currency units for balance and point value (mind cent accounts) |
| `stop_loss_points` | risk | Legacy stop; also the fallback when `point` is missing |
| `take_profit_points`, `daily_loss_percent`, `open_risk_percent`, `stop_level_points` | risk | Optional guards; skipped (and reported) when absent |
| `trend`, `setup_score` | opportunities | Legacy path, used only when `bars` is absent |

## Design rules

* **Separation:** detection never picks prices or sizes; timing never sizes; risk never changes direction.
* **No look-ahead:** indicators are causal, swing points count only once confirmed by later bars, and
  forecasts must be built on the same last candle as the snapshot.
* **Fail closed:** invalid or missing data rejects; unexpected errors reject and are logged.
* **Idempotent:** the same snapshot gives the same verdict; the executor de-duplicates on
  `stages.entry_timing.idempotency_key` (`entry:<signal_id>`).
* **Learning is advisory:** findings never modify thresholds. Use `summary(as_of=...)` /
  `review(as_of=...)` when replaying history so statistics cannot see the future.
* **Thresholds are untuned priors** (`OpportunityConfig`, `EntryTimingConfig`, `RiskConfig`).
  Validate them out-of-sample before trusting them.

## Django decision integration

The Django decision endpoint (`ea_bridge/decision.py`) passes validated completed M15 candles
(oldest first), the symbol/timeframe, current bid/ask/point, and account-sizing inputs into
`SignalFlowPipeline`. Because the `bars` field is present, opportunities use the structural path;
legacy `trend` and `setup_score` values remain diagnostics only.

The endpoint persists one `StrategyDecisionRecord` per symbol/completed-bar timestamp and refreshes
that record when the quote changes within the same candle. The record captures the structural stage
results and market snapshot for audit. The endpoint is advisory: `trading_authorization=False` and
`execution_enabled=False`; EA authorization gates remain authoritative. Daily-loss baseline and
account-state checks fail closed. Statistical forecasting remains opt-in and off by default.

Durable outcome storage for the learning module still needs a store implementing `add`, `get`, and
`all`. The structural thresholds are untuned priors and require out-of-sample and forward validation;
passing tests does not establish profitability.

## Tests

```
python manage.py test ramon_pipeline.signalflow
```
