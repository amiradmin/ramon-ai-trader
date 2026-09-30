# Offline Chronos covariates and Entry validation

This experiment changes no live service configuration, EA parameters, model
activation pointer or inference artifact. It uses a consistent SQLite backup
and writes reports under `data/research`. It is separate from shadow-role
training: a successful validation run does not promote or retrain live roles.

## Run

```bash
cd ~/Documents/Presentation/ramon-ai-trader
git fetch origin research/chronos-covariates-entry-validation
git switch research/chronos-covariates-entry-validation
git pull --ff-only origin research/chronos-covariates-entry-validation
docker compose build tools
bash scripts/research_ramon.sh
```

The running model container stays on its current image. Do not restart it to
run this offline experiment. MT5 does not need to be recompiled.

The runner bind-mounts the current checkout's `src` into its isolated tools
containers. If the existing tools image already has the required dependencies,
a rebuild is unnecessary (including when a Docker registry mirror is unavailable).

Reports:

- `data/research/entry-validation.json`
- `data/research/chronos-covariates.json`

Entry validation runs first and does not load Chronos weights. The Chronos
comparison loads the currently named checkpoint once, sharing its pipeline
between price-only and covariate forecasts. CPU execution can take time. The
active model file is read if present; it is never modified.

Comparison needs at least 600 completed M15 candles with recorded broker
spreads throughout the last 20%. Missing spreads cause a clear error; no assumed
spread fills the gap. Import actual MT5 history if needed. Historical broker fees
are inferred when present; missing fee telemetry remains explicitly unavailable.
An explicit fee estimate may be supplied using `--cost-r VALUE` (initial R per
round trip). Spread is already accounted for; do not include it in that estimate.

If a few recorded spreads are missing, an explicit subset experiment is available:

```bash
bash scripts/research_ramon.sh --skip-missing-spread-windows
```

All models use the same eligibility mask: a scheduled opportunity is excluded
if its decision candle or any candle in its complete possible execution horizon
has a missing spread. Candles stay in chronological context; no spread is
imputed and no time axis is compressed. The report records missing bars,
excluded scheduled windows and remaining eligible windows. Model-specific
position occupancy still determines actual traded opportunities. Missingness
can be systematic, so this result describes only the available-data subset and
does not establish performance during excluded periods. Strict mode remains
the default. `--coverage-only` prints data diagnostics without loading weights.

## Chronos comparison

Three models run on the same chronological final 20%, with fixed entry/exit
settings, recorded spreads and independent single-position paths:

1. Fixed momentum baseline.
2. Existing price-only Chronos.
3. The same Chronos checkpoint with historical candle range, body, return and
   causal rolling true-range mean (`atr14`) as past-only covariates.

The target and all covariates have identical historical lengths. No future OHLC,
ATR or returns are supplied. Tick volume, historical news and verified UTC
session features are unavailable in the current stored history and are not
invented. Forecast quantile shape, ordering, positivity and finiteness are
checked. The adapter follows the official Chronos-2 dictionary input API:
`target`, `past_covariates`, and `predict_quantiles`.

Primary references reviewed:

- https://github.com/amazon-science/chronos-forecasting
- https://github.com/amazon-science/chronos-forecasting/blob/main/src/chronos/chronos2/pipeline.py
- https://www.amazon.science/blog/introducing-chronos-2-from-univariate-to-universal-forecasting

The Amazon Science article describes in-context use of related series and
covariates without updating model weights. This experiment uses that inference
capability; it does not claim continual training from trades. Benchmark win rates
refer to forecasting comparisons, not the probability of winning a gold trade.

Replay uses closed M15 OHLC, next-bar fills and stop-first ordering on ambiguous
bars. It does not reproduce gaps, tick-level slippage, broker min-lot money
sizing, live TP stages, cooldown or profit-protection logic. Compare net R and
drawdown; these are descriptive research results, not MT5 live P/L. The model's
pretraining data overlap is unknown. A fresh forward period is required before
promoting any variant, even if this holdout looks better.

## Entry validation

Uses actual clean closed outcomes joined to their entry decision. The new offline
selection additionally excludes `manual_close` and `manual_dashboard_close`
even where historical rows were marked LEARNABLE. Existing live trainer selection
is unchanged. WAIT signals have no observed trade outcome and are not labeled.

At least 100 eligible examples are required. The chronological split is 50%
training, 25% calibration, 25% untouched test, with crossing outcome intervals
purged. Entry training retains the existing >=40 examples / >=10 per class
gate. Sigmoid calibration is fitted only on the later calibration window, and
needs >=40 examples / >=10 per class; otherwise its absence is reported.
No test labels influence the fitted probabilities.

The report includes Brier scores, probability-bin observed win rates, baseline
opportunity net R/drawdown, and fixed thresholds 0.5/0.6/0.7 with counts of
rejected winners and losers. It selects no threshold and authorizes no
promotion. Filtering historical executed opportunities is not a simulation of
new replacement trades after a veto; EA policy changes and mixed MAIN/SMALL
samples also limit generalization. Times remain recorded broker-event times,
not guessed UTC. Run a new prospective period after any parameter selection.

## Validation

Tests use deterministic forecast fixtures to verify the official API contract,
causal input construction, database preservation, spread requirements, temporal
purging and isolation of test labels. Real checkpoint inference and profitability
on the user's database must be measured by running the commands above.

## Prospective paired comparison

The historical replay can place trades at different times for each model.
To test whether Chronos improves forecasts at identical opportunities, run a
separate forward worker while the existing MT5 EA and model service are running:

```bash
git pull --ff-only origin research/chronos-covariates-entry-validation
bash scripts/forward_research.sh watch
```

Leave this terminal running. Ctrl+C stops the research worker. There is no cron,
automatic restart, EA recompilation, image rebuild or live-service restart.
The wrapper reads the current source and limits the independent tools container
to half of one CPU and one BLAS thread on the HP Mini. It shares the host's CPU
and memory with live inference; the limit reduces CPU contention but cannot
remove it. Actual real-checkpoint runtime must be checked on the user's machine.

The worker loads one checkpoint, then freezes its resolved revision, settings,
implementation hash and UTC start boundary in `data/research/forward-v1.sqlite3`.
All three models forecast the same frozen 256 completed candles, once per newest
M15 candle, for four future closes. There is no fitting, historical prediction
backfill, order placement, model promotion or write to the source history DB.
Resuming the same ledger retains the start boundary. Changed weights, settings
or source implementation require a new ledger filename rather than mixing runs.

Input freshness is checked against the latest live `decision_samples` row and
its quote, both within 120 seconds. The quote must have arrived after the run
boundary. Broker timestamps are converted using agreeing recorded deal offsets
in the prior 24 hours; an offset is never guessed from the machine clock.
When evidence is missing, conflicting or stale, the log reports a waiting status.
This may pause collection when there have been no recent trades. The stored
completed history must end at the live sample's signal candle. Both machines'
clocks must be synchronized. Forecasts must all finish at least 30 seconds before
the first target candle closes; slow forecasts save no partial comparison.

Each pair preserves its entire OHLC context, quote/spread provenance, context
hash, checkpoint revision in the manifest and all forecasts. Future bars are
read only for evaluation after all four target candles have closed. Missing
target timestamps (including weekend/session gaps) exclude the entire pair from
every model rather than compressing time. Outcomes are frozen at first maturity;
later history revisions cannot overwrite saved labels. Historical spread gaps
do not prevent price forecast scoring: this is not a trade replay, and no future
spread or fee is imputed.

`data/research/forward-v1.json` updates with recorded, pending, mature and excluded
pair counts; path and endpoint MAE; ATR-normalized path error; endpoint quantile
pinball loss and 80% interval coverage; direction accuracy; and a descriptive
direction subset where predicted movement exceeds the observed quote spread.
Direction compares the endpoint to the last completed close, with flat targets
excluded and denominator counts shown. The subset does not measure net returns.
A no-change forecast supplies an additional error reference: relative MAE below
1 beats that reference on this sample. All models are scored on identical mature
pairs. No winner, confidence interval, profitability or promotion is asserted.
Four-bar horizons overlap, so sample counts are not independent trade counts.

To refresh the report without loading model weights:

```bash
bash scripts/forward_research.sh evaluate
```

To start a distinct experiment after a checkpoint/code change:

```bash
bash scripts/forward_research.sh watch \
  --ledger /data/research/forward-v2.sqlite3 \
  --out /data/research/forward-v2.json
```

The `record` action attempts one fresh pair and reports its status; `watch` is
the normal way to collect new samples. The worker writes only its separate ledger
and report. Forecast errors and direction accuracy must be followed by a separate
execution-policy validation with realistic costs and the real EA exits before
they can support a trading change.
