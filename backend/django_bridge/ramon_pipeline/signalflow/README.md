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
The backward-compatible shims `ramon_pipeline.arima` / `ramon_pipeline.sarima` re-export the
forecast functions from `signalflow.forecasting`; existing management commands and tests can keep
using their old import paths. The Redis stream code in `ramon_pipeline/pipeline.py` remains separate.

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

## Wiring status

The Django decision endpoint (`ea_bridge/decision.py`) still sends only `trend` / `setup_score`, so it
uses the legacy path. To use the structural path add `bars`, `symbol`, `timeframe`, `bid`, `ask` and
`point` to its market dict. Durable outcome storage needs a store implementing `add`, `get`, `all`.

## Tests

```
python manage.py test ramon_pipeline.signalflow
```
