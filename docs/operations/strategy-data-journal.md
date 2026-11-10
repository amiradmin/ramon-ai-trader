# Strategy evidence and model datasets

Independent strategy evaluations are append-only `DecisionEvaluation` rows in PostgreSQL, including WAIT decisions and rejected risk verdicts. Commands link to their exact evaluation. Closed broker outcomes are stored separately in `LearningTradeOutcome`; their receipt timestamp controls when a label becomes available. Market bars remain separated by source, symbol and timeframe. Docker console output is operational logging, not a training dataset.

New independent-worker evaluations also preserve all input candles, their database receipt times, quote values and time, telemetry receipt time, an input SHA-256, effective strategy thresholds and source/configuration file SHA-256 fingerprints. This begins after deploying the updated workers; historical evidence cannot be reconstructed with certainty. Database backups must include these tables and market bars. Never delete named volumes to rotate logs.

## Export one strategy/account scope

The export runs read-only and never enables orders or starts training. Use real scope values from the local configuration; keep exports private because they contain account identifiers and financial data.

```bash
docker compose exec django python manage.py export_strategy_journal \
  --source YOUR_EA_INSTANCE --account-login YOUR_DEMO_LOGIN \
  --server YOUR_DEMO_SERVER --symbol XAUUSD_o \
  --setup TREND_PULLBACK --output /app/media/trend-pullback-evidence.jsonl
```

The media directory is the persistent `learning_models` volume. Choose a new output path for every export. Optional `--as-of 2026-11-10T12:00:00Z` limits both evaluations and broker-label receipts. `--limit` defaults to 10000 evaluations; the summary reports truncation. The command prints the file checksum and counts. Other allowed setups are the seven setup names shown on the dashboard. The scalper has its separate `export_scalper_journal` command.

Features, risk verdicts, command IDs and broker labels are separate fields. A broker label requires the exact evaluation-linked OPEN command, matching source/account/server/symbol, command tag and decision ID. Unfilled orders and WAIT evaluations are not losses. Legacy evaluations without input evidence remain exportable for diagnostics but are marked ineligible. Paper results must never be treated as broker P&L. Eligibility means linked evidence is present and causal checks passed; it does not establish sufficient sample size, correct fees, or statistical validity.

For training, use decision-time features only. Split chronologically; purge overlapping holding periods across training/validation/test boundaries. Fit preprocessing only on the training period. Preserve the export checksum and policy fingerprints with every model. Evaluate net returns, costs, drawdown, coverage and sample size on a later untouched period before considering a model useful. These changes do not automatically train models or change trading permissions.

## Operational log retention

Compose uses Docker `json-file` rotation for every service: five files of up to 20 MB per container. Container recreation activates this setting. Logs are disposable diagnostics; database evidence and exported datasets are the durable research record.

```bash
docker compose logs --since 1h trend-pullback
docker compose up -d --build --wait --wait-timeout 180
```
