# ARIMA candle forecasting

The first ARIMA implementation is an **offline research command**. It reads completed candles from the existing Django `MarketBar` table; it does not call the EA, send trading instructions, or place orders.

## Data flow

1. The EA emits a completed candle through the existing authenticated events endpoint.
2. Django queues the event to Redis Streams when pipeline ingestion is enabled.
3. `consume_ramon_streams` persists the candle into `MarketBar`.
4. `run_arima_forecast` loads candles in timestamp order, checks data sufficiency and timestamp gaps, evaluates ARIMA on a chronological holdout, then forecasts one candle close.

## Run it

From the repository root after rebuilding the Django image/installing updated requirements:

```bash
docker compose build django worker
docker compose run --rm django python manage.py run_arima_forecast \
  --symbol XAUUSD_o --timeframe M15 --limit 1000 --test-size 20
```

Optional arguments include `--source EA_INSTANCE_ID` to restrict the data source and `--p`, `--d`, `--q` to test another ARIMA order. The default is ARIMA(1,1,1).

The JSON output includes the latest candle close/time, forecast close and 95% model confidence interval, chronological holdout MAE/RMSE/directional accuracy, sample sizes, and the number of missing expected candle intervals.

## Interpretation and limitations

- Candles are sorted oldest-to-newest. The holdout is the latest segment; training never includes future observations.
- Missing intervals are reported, not fabricated or interpolated.
- MAE and RMSE are in the symbol's price units. Directional accuracy compares predicted and observed close-to-close direction.
- The default ARIMA(1,1,1) is a baseline, not a claim of optimality. Compare orders on the same time-ordered holdout and inspect residuals before choosing a production model.
- Forecasts are currently printed to the command output, not persisted as forecast records. Persistence and scheduled evaluation should be added only after the baseline has been checked against real candles.
- This command is not connected to trade execution. A forecast alone must never authorize an order.
