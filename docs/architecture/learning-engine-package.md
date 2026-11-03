# Learning Engine package architecture

The assisted-learning implementation is organized under
`backend/django_bridge/ramon_pipeline/learning/`. The original
`ramon_pipeline.signalflow.learning` implementation remains the canonical
outcome-validation and statistical compatibility layer; existing imports and
SignalFlow behavior are preserved.

## Modules

| Module | Responsibility |
| --- | --- |
| `models.py` | Typed domain payload contracts. Durable Django ORM models remain in `ea_bridge/models.py`. |
| `outcome_collector.py` | Validates and records outcomes through the existing OutcomeStore contract. |
| `performance_analyzer.py` | Overall performance statistics and confidence-aware summaries. |
| `decision_analyzer.py` | Decision-link coverage; missing links are never guessed. |
| `entry_exit_analyzer.py` | Aggregates only supplied execution metrics; missing values remain unavailable. |
| `regime_analyzer.py` | Segments outcomes by recorded market regime. |
| `mistake_detector.py` | Exposes conservative, sample-gated statistical findings. |
| `strategy_evaluator.py` | Compares recorded strategy profiles only when each meets a minimum sample. |
| `optimization_engine.py` | Compares baseline/candidate out-of-sample evidence and can only mark a candidate for human review. |
| `recommendation_engine.py` | Formats findings as advisory review hypotheses. |
| `engine.py` | Orchestrates the analyzers into one read-only report. |

## Dashboard

The read-only Learning Engine dashboard is served at `/api/ea/v1/learning/dashboard`. It displays overall performance, recent cumulative realized PnL, decision attribution coverage, regime segments, statistical findings, execution-metric availability, strategy-profile sample sufficiency, human-review recommendations, and recent closed trades. Recommendation generation and review use the existing staff-protected endpoints; accepting a recommendation authorizes a backtest only.

## Integration and compatibility

- `ea_bridge.models.LearningTradeOutcome` and `LearningRecommendation`
  remain the durable database models; no schema migration is required.
- `ea_bridge.learning_store.DjangoOutcomeStore` remains the Django persistence
  adapter and retains unique trade IDs/idempotent ingestion.
- Existing imports from `ramon_pipeline.signalflow.learning` remain supported.
- The package uses the existing SignalFlow statistics rather than introducing
  a second performance-calculation implementation.

## Safety boundary

This package does not import or modify command authorization, risk verdicts,
decision thresholds, or EA execution. Recommendations are advisory.
Optimization requires adequate baseline and candidate sample sizes and better
out-of-sample expectancy without worse drawdown; even then the only result is
`ELIGIBLE_FOR_HUMAN_REVIEW`. It does not edit parameters, create commands,
authorize trades, or deploy a strategy.
