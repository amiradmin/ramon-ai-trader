# ARIMA forecast observer

The main Ramon dashboard now has an **ARIMA · next 15-minute close** panel near
the top. A separate `forecast-worker` saves forward ARIMA(1,1,1) forecasts and
checks them against actual completed candles. It never creates commands, changes
thresholds, or approves/vetoes a trade.

## What appears in the panel

- The forecast close and its nominal 95% model prediction interval.
- The point forecast's direction, with an explicit indication when the interval
  spans both upward and downward moves from the last close.
- The exact UTC time at which the target candle closes and when the forecast was
  issued. Candle timestamps in the database represent candle **opens**: an origin
  at 08:30 completes at 08:45, and forecasts the candle opening 08:45, closing 09:00.
- Automatically tracked price error, direction accuracy, interval coverage, and
  a comparison with predicting that the last close stays unchanged.
- Recent forecast/actual pairs, including skipped or failed attempts.

Errors are in symbol price units, not account P&L. The prediction interval is not
a calibrated probability that a BUY/SELL trade will win. The model's direction
agreement with a trade is descriptive and applies only to the same source,
symbol, and signal candle. It does not feed back into candidate ranking or orders.

## Forward-only lifecycle

1. Every 15 seconds, discover connected demo EA feeds with fresh telemetry.
2. Read up to 256 completed, correctly aligned M15 closes from the same source and
   symbol. Exclude forming/future candles, records not yet received, off-grid
   timestamps, and fractional-second timestamp artifacts. At least 128 observations
   are required. Missing clock intervals are counted; closures/gaps are not filled
   with fabricated prices. The model treats observations as trading bars, rather
   than claiming to reconstruct a continuous wall-clock price series.
3. Claim one attempt per source/symbol/origin/model version using database
   uniqueness. Fit outside the transaction and the web request. The existing
   retrospective research command is not invoked on the live decision path.
4. Publish only converged, finite forecasts with a valid positive interval. A fit
   must finish within two minutes of the origin candle completing and before the
   target closes. A late startup waits for the next candle; it does not backfill
   old forecasts into the live accuracy figures.
5. After the target closes, score only the exact source/symbol/timeframe/target
   timestamp. Late telemetry is allowed up to one hour. Never substitute a later
   candle for a missing target. Save the actual close once; later revisions do not
   rewrite an already-scored result.

The persistence baseline is the origin close saved alongside each prediction.
Metrics use the latest 100 forward-scored observations for the current model
version and feed. Errors, skips, uncompleted predictions, late predictions, other
feeds, and other model versions do not contribute. Before 20 scored predictions,
the panel says **Collecting results**; reaching 20 is a display threshold, not a
statistical certification. A complete 20-prediction sample takes at least five
trading hours after collection starts.

Worker interruptions leave a bounded claim which is marked failed on a subsequent
cycle. A failed or skipped attempt is not silently retried for the same origin;
the worker tries again when a new eligible origin arrives. No existing candle or
trade history is deleted.

## Configuration and operation

Defaults:

```text
RAMON_ARIMA_SHADOW_ENABLED=1
RAMON_ARIMA_SHADOW_WINDOW=256
```

Window sizes must be between 128 and 1000. A changed window has a separate model
version and therefore a separate accuracy sample. Disabling the observer does not
disable trading or erase its saved forecasts.

Deploy from the repository root:

```bash
docker compose up -d --build django forecast-worker
docker compose logs --tail=30 forecast-worker
```

The Django startup command applies the additive `0010_arima_shadow_forecasts`
migration before serving requests. The forecast worker waits for healthy Django
and TimescaleDB. It needs no new model dependency, external forecasting API,
credentials, Redis queue, or MT5 EA update. The existing ingestion and learning
workers may continue running.

Read-only status endpoint:

```text
GET /api/ea/v1/forecast/arima
```

A GET reads saved results and never fits a model. The panel polls independently of
the trading dashboard so a forecast failure does not block decision monitoring.
It is part of the existing localhost-only dashboard surface, with no new public
port exposed. For diagnostics, one foreground cycle is available:

```bash
docker compose exec forecast-worker python manage.py run_arima_shadow_worker --once
```

The worker's per-origin uniqueness makes a concurrent diagnostic cycle safe.
On first startup, **Waiting for next candle** is expected if the current origin is
already more than two minutes old. The first accuracy result arrives after a
timely forecast's target closes; historical backtest scores are not preloaded.

## Evidence and limits

The implementation uses [statsmodels' ARIMA fitting API](https://www.statsmodels.org/stable/generated/statsmodels.tsa.arima.model.ARIMA.fit.html)
and [out-of-sample forecasts with prediction intervals](https://www.statsmodels.org/stable/generated/statsmodels.tsa.arima.model.ARIMAResults.get_forecast.html).
Forward error tracking does not establish a trading edge or account for spread,
commission, swap, fill quality, or stop/target behavior. Keep any decision influence
as a separate, measured change after collecting forward evidence.

Tests cover fit input boundaries, idempotent issuance, completion/receipt timing,
late-fit exclusion, exact-target settlement, restart recovery, missing targets,
incremental next-candle processing, baseline arithmetic, feed/version isolation,
and dashboard reads without model fitting or order writes. The repository's
existing Django/PostgreSQL checks remain the canonical test surface; Ruff/ty/
basedpyright configuration is still a separate tooling gap.
