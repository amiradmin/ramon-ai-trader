# Ramon Learning Engine — Assisted Learning

## Purpose
The Learning Engine closes the feedback loop between Django strategy decisions and completed broker trades. It measures outcomes, attributes them to the original decision where possible, identifies statistically supported patterns, and presents hypotheses for human review.

It is advisory only. It does not issue orders, authorize commands, mutate SignalFlow thresholds, or deploy strategy changes.

## Data flow
1. The authenticated EA bridge posts a completed trade outcome to `POST /api/ea/v1/learning/outcomes`.
2. Django validates the payload through the existing SignalFlow outcome validator.
3. `DjangoOutcomeStore` persists an immutable row keyed by broker deal/trade ID. Exact re-delivery is idempotent; conflicting data is rejected rather than overwritten.
4. When a `decision_id` is supplied, Django snapshots the matching `StrategyDecisionRecord`. A known `broker_ticket` can also be resolved through `OrderIntentRecord`.
5. The read-only overview computes performance, side/regime/exit-reason segmentation, confidence-aware findings, and execution diagnostics.
6. A Django staff user may generate recommendations and accept one for a controlled backtest or reject it.

## Outcome payload
Required:
- `trade_id` (or `deal_ticket`): stable unique broker deal/trade identifier.
- `realized_pnl`: realized net PnL in account currency, already net of broker commission, swap and trading costs.
- `closed_at`: timezone-aware ISO-8601 close timestamp.

Useful optional fields:
- `decision_id`, `broker_ticket`, `instance_id`, `account_login`, `symbol`
- `opened_at`, `risk_amount`, `side`, `regime`, `exit_reason`, `setup_type`, `signal_id`, `score`, `strategy_profile`, `model_version`, `entry_timing`
- `mae`, `mfe`, `entry_efficiency`, `exit_efficiency`, `spread_points`, `slippage_points`, `commission`, `swap`

All timestamps should be UTC. MAE/MFE and execution metrics are only analyzed when supplied; missing values are not assumed to be zero. Efficiency fields must be in the 0–1 range. PnL must be net to avoid double-counting costs.

## API
- `POST /api/ea/v1/learning/outcomes` — bearer-authenticated outcome ingestion.
- `GET /api/ea/v1/learning/overview` — read-only metrics, findings, recommendation queue and recent outcomes.
- `GET /api/ea/v1/learning/dashboard` — Learning Engine dashboard.
- `POST /api/ea/v1/learning/recommendations/generate` — Django staff only.
- `POST /api/ea/v1/learning/recommendations/<id>/review` — Django staff only; `ACCEPT` means accept for backtest, not production deployment.

## Assisted workflow and safety
- The existing SignalFlow sample-size floor and confidence bounds are retained.
- Small samples produce an explicit insufficient-data finding, not an optimization instruction.
- Recommendations are deduplicated by evidence fingerprint.
- Accept/reject decisions are audited with reviewer and timestamp.
- There is no code path from recommendation review to `DecisionThresholdConfig`, `EACommand`, or command authorization.
- An accepted hypothesis must be implemented separately, backtested out-of-sample with realistic execution costs, reviewed, and explicitly deployed through the normal controlled release process.

## Six implementation stages
1. Durable outcome collector and idempotent storage.
2. Performance metrics and evidence summary.
3. Decision-to-outcome attribution using immutable decision snapshots.
4. Entry/exit diagnostics using supplied MAE/MFE and efficiency fields.
5. Market-regime and setup segmentation.
6. Human-reviewed recommendations for future backtests; parameter optimization remains disabled in assisted mode.

## Integration boundary
The new endpoint is ready to receive normalized completed-trade events. The MT5 EA event schema must be inspected and mapped explicitly before changing EA emission; no assumptions are made about broker ticket semantics or the EA's current close-event payload.


## Modular Python package

The analysis layer is organized in `backend/django_bridge/ramon_pipeline/learning/`. It separates outcome collection, performance, decision-link coverage, entry/exit diagnostics, regime segmentation, mistake detection, strategy-profile evaluation, optimization evidence checks, and recommendation formatting. The existing `signalflow.learning.Learning` remains the compatibility/statistics core; the Django ORM models and `DjangoOutcomeStore` remain in `ea_bridge` and are not duplicated. The read-only overview API exposes the combined `analysis` report. See [the package architecture](learning-engine-package.md) for module contracts.

## MT5 completed-position integration (EA v1.06)

The pinned production source is `mt5/RamonCommandEA_v1.06.mq5`. The EA scans broker deal history on its timer and submits only positions that have fully closed. It does not send each partial-close deal as a separate trade outcome.

For each closed hedging position, it:
- Selects all broker deals associated with the position identifier.
- Sums profit, swap, commission and fee across the position to produce net realized PnL.
- Uses the earliest entry deal and latest exit deal for open/close timestamps and BUY/SELL direction.
- Sends the broker opening-order ticket, account login, instance ID, symbol and exit reason.
- Sends commission and swap as diagnostics; it does not fabricate MAE, MFE, slippage, entry efficiency, exit efficiency, or initial risk when those values are not available from broker history.
- Uses a terminal Global Variable cursor (account + EA instance) and flushes it after each accepted event. A failed HTTP delivery leaves the cursor before that close event so the EA retries after the next timer tick.
- Relies on Django's unique `trade_id` and idempotent outcome store to tolerate a repeated delivery after an EA restart or lost response.

The EA starts with a 90-day broker-history lookback on first installation. It then resumes from its persistent deal-time/deal-ticket cursor. The history cursor is local to the MT5 terminal; reinstalling MT5 or clearing terminal Global Variables can reset the cursor and cause historical records to be re-sent. Django rejects exact duplicates without double-counting them.

### Decision attribution

The EA first includes the full `RamonCmd:` comment command ID when the broker preserves it. Since brokers may truncate order comments, it also sends the opening order ticket. Django resolves that ticket against the acknowledged `EACommand.result.broker_ticket`, scoped by EA instance and account, and snapshots the original `StrategyDecisionRecord`.

If a trade was opened manually or its command acknowledgement was never persisted, the outcome is still recorded but appears as unlinked. No decision or strategy metadata is guessed.

### Supported boundary and validation

This implementation targets the project's hedging-account workflow. Netting reversal deals (`DEAL_ENTRY_INOUT`) are not currently split into separate logical trades and are not emitted as completed-position outcomes. The current broker-server UTC offset is applied to historical open/close timestamps; positions spanning a broker daylight-saving transition need broker-specific historical timezone evidence for exact conversion.

The deployment script now compiles and installs only v1.06. GitHub's Windows MetaEditor workflow compiles the tracked command EA sources, while static guards verify the pinned production source and the new learning-delivery contract. A successful CI build is not a substitute for local MT5 demo validation.
