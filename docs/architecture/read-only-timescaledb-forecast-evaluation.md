# Read-only forecasting evaluation against Ramon MarketBar data

The `research_forecast` Django management command evaluates forecasting models using the existing
`MarketBar` rows in the configured database (the project's PostgreSQL/TimescaleDB service in Docker).
It does not create, update, or delete rows and does not call the strategy decision,
risk, authorization, order-intent, or EA command paths.

## Run locally

From the repository root, with the normal Ramon Compose services running:

```bash
docker compose exec django python manage.py research_forecast \
  --symbol XAUUSD_o \
  --timeframe M15 \
  --limit 1000 \
  --test-size 20 \
  --horizon-bars 4
```

If the feed has multiple `MarketBar.source` values, select the intended feed explicitly:

```bash
docker compose exec django python manage.py research_forecast \
  --symbol XAUUSD_o --timeframe M15 --source YOUR_FEED_NAME --limit 1000
```

Available models can be selected with `--models`:
- `arima`: existing rolling one-step ARIMA evaluation.
- `sarima`: existing rolling one-step SARIMA evaluation.
- `ridge`: Ridge regression on completed OHLCV-derived features.
- `random_forest`: Random Forest regression on the same features.

For regression models, install the optional research extra in the research environment
(not the production Django runtime):

```bash
pip install -e ".[research]"
```

The command prints a JSON report containing the exact symbol, timeframe, source filter,
number and time range of candles read, model metrics, failures, and explicit
`trading_authorization=false`, `strategy_mutation=false`, and `database_writes=false` markers.

## Evaluation cautions

- Use completed candles only. The query reads stored MarketBar records in timestamp order.
- Regression uses a chronological holdout, purges overlapping forward-target rows,
  and compares against a zero-return baseline.
- ARIMA/SARIMA retain their existing rolling one-step evaluations and persistence baselines.
- Results are research diagnostics, not signals. A favorable forecast is not a trade recommendation.
- Confirm candle coverage, source identity, duplicate/gap behavior, and out-of-sample stability
  before interpreting metrics. This command does not repair or synthesize missing candles.
- The command is read-only; it intentionally does not persist model forecasts or change live configuration.
