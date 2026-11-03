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
