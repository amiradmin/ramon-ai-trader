# Ramon Historical Lab — external XAUUSD M15 data

This path is intentionally isolated from Ramon's live symbol and EA behavior.

## Why

The Kaggle XAUUSD archive can be used to expand historical M15 coverage for
research, regime analysis and walk-forward validation. It must not be treated as
LiteFinance execution history because the source broker, spread and candle source
can differ.

External bars are therefore imported under the default synthetic symbol
`XAUUSD_KAGGLE` and `spread_points=0` means **unknown external spread**, not a
zero-spread trade.

## Audit the uploaded archive

```bash
uv run python -m ramon.kaggle_history /path/to/archive.zip --audit-only
```

Expected archive member for Ramon is `XAU_15m_data.csv`.

The archive timestamps are treated as source wall-clock time. This dataset defaults
to `UTC+3`, and the importer converts them to canonical UTC epoch seconds before
storage. Override only when auditing a source with a different documented offset:

```bash
uv run python -m ramon.kaggle_history /path/to/archive.zip \
  --audit-only --source-utc-offset-hours 3
```

The audit reports:

- invalid OHLC/date rows
- duplicate timestamps
- non-monotonic rows
- M15 timestamp alignment
- zero-volume rows
- regular 15-minute intervals
- larger gaps and the largest observed gap

A gap is reported, not automatically filled. Weekend/session gaps must not be
fabricated into synthetic candles.

## Import into a separate research database

Recommended:

```bash
uv run python -m ramon.kaggle_history /path/to/archive.zip \
  --db /data/ramon_kaggle_m15.sqlite3 \
  --symbol XAUUSD_KAGGLE
```

The importer audits before writing and refuses structurally dirty data by
default. `--allow-dirty` exists only for controlled investigation.

Do not import this dataset as `XAUUSD_l`. Keeping the external source under a
separate symbol prevents accidental mixing with LiteFinance live/execution data.

## Verified dataset snapshot used during implementation

The supplied archive was checked locally before this importer was committed:

- timeframe: M15
- rows: 494,235
- valid rows: 494,235
- duplicate timestamps: 0
- non-monotonic rows: 0
- misaligned timestamps: 0
- zero-volume rows: 0
- source timezone used: UTC+3
- first bar after UTC normalization: 2004-06-11 04:15Z
- last bar after UTC normalization: 2026-01-30 20:45Z
- regular 15-minute intervals: 485,714
- larger gaps: 8,520
- largest gap: 2,793,600 seconds

These figures describe this archive only; re-run the audit whenever the source
file changes.

## Next research step

Use this database for Ramon-only M15 experiments and walk-forward comparisons.
Do not change the live EA or production decision thresholds based solely on
in-sample results from this external broker dataset. Final validation should
still include held-out LiteFinance data and realistic execution assumptions.


## Historical benchmark lab

The external M15 database can be evaluated without changing the live EA:

```bash
PYTHONPATH=src python3 -m ramon.historical_benchmark \
  --db data/ramon_kaggle_m15.sqlite3 \
  --symbol XAUUSD_KAGGLE \
  --folds 5 \
  --stride 4 \
  --fallback-spread 42 \
  --output data/ramon_historical_benchmark.json
```

The first benchmark intentionally compares only two simple, non-trained reference
forecasters:

- `previous_bar`: projects the most recent completed M15 bar move.
- `momentum_4bar`: projects the mean of the last four completed-bar changes.

The first 20% of bars are excluded from scored results and remain available only
as historical context. The remaining 80% is reported both as one continuous
evaluation and as chronological, non-overlapping folds.

Each model report includes:

- trades, BUY and SELL counts
- wins, losses and timeouts
- resolved win rate
- timeout rate
- gross positive/negative R
- profit factor
- mean R and net R
- max drawdown in R
- BUY/SELL metrics separately
- calendar-year metrics
- fold-by-fold metrics

These baseline models are not fitted, so the folds are chronological evaluation
windows rather than train/test fits. When a trainable model is added, it must be
trained only on observations strictly earlier than the evaluated fold.

For this external dataset, `spread_points=0` means unknown. The benchmark therefore
requires an explicit fallback spread (42 points in the example). This is a
research assumption, not a claim about historical Octa or LiteFinance spreads.

The benchmark remains deliberately separate from RANGE execution, quote-level
micro context, slippage and live EA exit management. A favorable result here is
not sufficient evidence for a live configuration change.


### Directional accuracy and horizon matrix

The benchmark also separates raw directional skill from trade management.

For each model it reports `directional_accuracy` at M15 horizons `1,4,8,16`.
This compares the sign of the model forecast with the sign of the future close
move. It does not use stop loss, take profit, entry filters, or market-state
routing.

It also reports a `horizon_matrix` for `1,4,8,16` bars. That section replays
the same model with each holding horizon while keeping the same ATR-based stop
and target settings. This is intended to reveal whether a high TIMEOUT rate is
primarily caused by an overly short holding horizon.

Override the analysis horizons when needed:

```bash
PYTHONPATH=src python3 -m ramon.historical_benchmark \
  --db data/ramon_kaggle_m15.sqlite3 \
  --symbol XAUUSD_KAGGLE \
  --analysis-horizons 1,4,8,16 \
  --fallback-spread 42 \
  --output data/ramon_historical_benchmark.json
```


### Contrarian baselines and regime-conditioned direction

The lab now includes mirrored versions of both reference forecasts:

- `contrarian_previous_bar`
- `contrarian_momentum_4bar`

A contrarian forecast mirrors the original forecast around the latest completed
close. This tests whether a persistently sub-50% directional signal contains a
stable mean-reversion clue after costs; it is not assumed to be profitable.

Each model also reports `regime_directional_accuracy`. Ramon's existing
price-only `assess_market()` classification is computed using only bars and
spread information available at the signal time, then raw forecast direction is
scored separately inside each state and horizon. This helps identify whether a
signal has conditional skill in states such as TREND, RANGE, BREAKOUT,
VOLATILITY_COMPRESSION, or UNCERTAIN even when aggregate accuracy is weak.

Regime-conditioned results are descriptive research. Require adequate sample
counts and consistency across chronological folds before treating any apparent
edge as meaningful.


### Regime trade profitability and fold stability

The lab now reports `regime_trade_matrix` for every model and analysis horizon.

Each market state includes trade-level:

- trade count
- resolved win rate and timeout rate
- profit factor
- mean R and net R
- max drawdown in R
- BUY/SELL counts

For each state the report also reruns the same horizon in every chronological
fold and records:

- profitable folds (PF > 1)
- folds with positive mean R
- folds containing trades
- per-fold trades, PF, mean R, net R and max drawdown

This is the preferred screen for candidate edges. A high directional accuracy
alone is insufficient; prioritize states with enough trades and consistent
positive trade metrics across multiple independent folds. These results still
use assumed external spread and coarse M15 OHLC execution, so they remain
research evidence rather than a live deployment rule.


### Chronos-2 screening in the same lab

Chronos can now be added to the same historical report without changing the live
EA:

```bash
python -m ramon.historical_benchmark \
  --db /data/ramon_kaggle_m15.sqlite3 \
  --symbol XAUUSD_KAGGLE \
  --folds 5 \
  --stride 4 \
  --fallback-spread 42 \
  --analysis-horizons 1,4,8,16 \
  --include-chronos \
  --chronos-model autogluon/chronos-2-small \
  --chronos-device cpu \
  --chronos-stride 128 \
  --chronos-cache /data/chronos_historical_forecasts.sqlite3 \
  --output /data/ramon_historical_benchmark_chronos.json
```

Chronos uses the project's existing `ChronosForecaster`. The default research
workflow may use a coarser `--chronos-stride` than the baseline stride for an
initial screen. The report records each model's stride and explicitly warns when
the Chronos stride differs. Any promising Chronos result should be rerun with
`--chronos-stride` equal to `--stride` before direct performance comparison.

Expensive forecasts are stored in a persistent SQLite cache keyed by model
identity, horizon and the exact historical close context. Re-running or resuming
the same experiment reuses cached forecasts. A model revision is part of the
cache identity so different checkpoint revisions are not silently mixed.

For the Docker setup, the tools image already contains the model dependencies and
shares the Hugging Face cache. The current checkout can be mounted into the tools
container so a rebuild is not required just to run new research code:

```bash
docker compose --profile tools run --rm \
  -v "$PWD:/workspace" -w /workspace \
  -e PYTHONPATH=/workspace/src \
  tools -m ramon.historical_benchmark \
  --db /data/ramon_kaggle_m15.sqlite3 \
  --symbol XAUUSD_KAGGLE \
  --folds 5 --stride 4 --fallback-spread 42 \
  --analysis-horizons 1,4,8,16 \
  --include-chronos --chronos-device cpu --chronos-stride 128 \
  --chronos-cache /data/chronos_historical_forecasts.sqlite3 \
  --output /data/ramon_historical_benchmark_chronos.json
```

A coarse Chronos screen is exploratory. External OHLC, assumed spread, different
sampling density and M15 execution limitations still apply.


### Terminal progress

Long historical benchmark runs now show a live progress bar on stderr by default.
It reports percentage, completed work units, elapsed time, ETA and the current
stage/model. JSON output remains clean on stdout and in `--output` files.

Example:

```text
[#########---------------]  37.42% | 3742/10000 | elapsed 12:41 | ETA 21:13 | chronos: regime directional H=8
```

Use `--no-progress` only when machine-readable stderr is required.
