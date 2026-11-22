# Ramon AI Trader

Ramon is a local MT5 gold-trading project whose **seven independent strategy containers own their decisions, entry checks, sizing and position management**. Shared Django APIs provide monitoring, telemetry and command delivery. The MT5 Expert Advisor (EA) is a telemetry/command bridge and broker-side executor; it must not become a second strategy engine.

> **Deployment entry point:** `scripts/update_build_ramon.sh` is the single-command local deployment. It starts and validates the Compose services and compiles/installs only `RamonCommandEA_v1.09.ex5`. GitHub Actions verifies the command EA source contract and MetaEditor compilation. Local deployment still needs to be run on the target machine.

## Current supported stack

The single `docker-compose.yaml` defines:

- `django`: Django API, bound to `127.0.0.1:8017` on the host.
- `ingest-worker`: Redis Streams consumer that persists queued events; replaces the old `ramon-worker` container.
- `trend-pullback`, `breakout-retest`, `failed-breakout-sweep`, `range-edge-fade`, `session-opening-range-breakout`, `volatility-compression-expansion`, `exhaustion-reversal`: independent M15 strategy containers. Each can issue orders only for the configured real cent account within the shared account budget.
- `scalper-worker`: existing independent M1/M5 scalper.
- `trend-worker`: existing H1/H4 paper-only research candidate.
- `forecast-worker`: forward ARIMA forecasts and automatic accuracy tracking; observation only.
- `news-worker`: automatic verified-offset weekly economic-calendar refresh for news entry locks.
- `candidate-worker`: candidate observation and paper outcome settlement.
- `learning-worker`: user-triggered model training.
- `redis`: event buffer.
- `timescaledb`: PostgreSQL/TimescaleDB durable store.

Dashboard: `http://127.0.0.1:8017/api/ea/v1/dashboard` contains a link to each strategy’s controls, positions and history. See [independent strategy operations](docs/operations/independent-strategy-containers.md).

Paper research results: `http://127.0.0.1:8017/api/ea/v1/research/paper-results` (linked from the main dashboard). Historical records and the existing journal API are preserved.

Historical simulator: `http://127.0.0.1:8017/api/ea/v1/simulator`.
Long-range M15 tests now run through the isolated `replay-backtest-worker` Compose
service and store job progress/results in the existing `learning_models` volume
(`/app/media/historical-replay-jobs`). The browser submits a CSRF-protected job
and polls progress. A selected range of up to 15,744 M15 candles (plus 256
warm-up) is supported for this first worker iteration; 20-year testing still
requires a segmented engine and substantially more imported historical data.
The simulator now supports isolated $1,000 versus $10,000 runs on matching
historical OHLC fingerprints, a complete virtual trade ledger (signal time,
entry, SL, TP, exit, volume, realized estimate), and replay chart entry/exit
markers. During date-bounded M15 research, the chart requests
`validated=1` so it displays the same grid-aligned candle subset as the
strategy backtest; other timeframes remain raw. This does **not** fix or
reconstruct the source records.

Audit off-grid historical source records without changing the database:
```bash
docker compose exec django python manage.py audit_replay_m15 \\
  --source ramon-command-ea-1 --symbol XAUUSD_o \\
  --start 2026-09-22 --end 2026-11-22
```
Inspect offsets grouped by the date the candles were ingested before
re-importing from verified MT5 open timestamps. The current telemetry
endpoint already validates exact UTC candle-open alignment; this audit
does not establish which older import path created the malformed records.
Historical data-quality validation excludes off-grid M15 candles and duplicate
slots without rewriting source rows or inventing replacement prices, and reports
accepted/excluded counts and remaining gaps. The isolated research path now
enables one configured supplemental setup per evaluation even when the normal
production configuration enables it only for demo research; standard risk caps
remain unchanged. Rejection diagnostics include actual risk-sizing explanations
where available. Historical conclusions from different cleaning policies or
setup profiles are not directly comparable.
The chart loads 2,000 candles per page and automatically fetches further pages
when Play/Step reaches the current end, while retaining a 30-candle viewport.
The full-period research result is displayed separately. A Trade Rejection
Analysis panel aggregates actual SignalFlow gate rejection reasons by strategy,
including opportunity, entry timing, risk sizing and invalid virtual fills. Backtests call the existing seven
strategy SignalFlow evaluators with assumed broker specifications and isolated
virtual execution; they never enqueue EA trading commands. These are estimates,
not live-worker/broker fill parity. To deploy, run
`docker compose up -d --build django replay-backtest-worker`; do not delete
named volumes.

Health check: `http://127.0.0.1:8017/healthz`

The Compose pipeline ingestion switch is off by default. The named database and Redis volumes contain persistent state; **never use `docker compose down -v` as a troubleshooting step**.

## Start all containers

From the repository directory, start all 17 services, including the seven independent strategy workers and their supporting services:

```bash
cd ~/Documents/Presentation/ramon-ai-trader-bridge
docker compose up -d --build --wait --wait-timeout 180
docker compose ps
```

No service names or profiles are needed. Compose starts the database and Redis first, waits for their health checks, then starts Django and the workers. Services without a health check are considered ready once running. Existing `.env` trading settings and persistent volumes are preserved. This command starts Docker services; MT5 and the command EA must also be running for broker execution.

## Setup and update

Prerequisites: Docker Engine with the Compose plugin, Git, curl, Wine, MetaEditor from the MT5 installation, and an existing local `.env` file. The file must supply the database password and the configured Django/EA secrets. Do not commit `.env` or paste bearer tokens into logs or chat.

For the existing clone at `~/Documents/Presentation/ramon-ai-trader-bridge`:

```bash
cd ~/Documents/Presentation/ramon-ai-trader-bridge && bash scripts/update_build_ramon.sh
```

If the clone lives elsewhere, set `RAMON_REPO_DIR` to its absolute path before running the script. Optional build settings include `RAMON_MT5_PREFIX`, `RAMON_MT5_TERMINAL_ID`, `RAMON_METAEDITOR`, and `RAMON_HEALTH_URL`; see [the update/build runbook](docs/operations/one-command-update-build.md).

The script validates the Compose configuration, builds/starts services, applies forward migrations, runs Django's system check, verifies Django/Redis/TimescaleDB health and worker status, backs up the current command EA, then compiles and installs only `RamonCommandEA_v1.09.mq5`. The legacy `Ramon_v0.62.x` strategy EA is deliberately excluded to avoid installing a second EA alongside the command architecture. The script does not launch MT5, attach an EA, change trading flags, or place orders. It stops rather than accepting a missing compiler log or a stale EX5 as a successful build.

## EA and API

The versioned command EA source is `mt5/RamonCommandEA_v1.09.mq5`. It defaults to:

- empty `DjangoBearerToken` (set only in MT5 Inputs on the local machine)
- no local strategy or risk-policy settings; the EA accepts an order only when Django supplies both `trading_authorization=true` and `execution_authorized=true`, and terminal/EA trading permissions allow it
- Django command creation and execution disabled server-side unless explicitly configured otherwise

Authenticated API routes are under `/api/ea/v1/`:

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `handshake` | Protocol handshake; never grants trading authorization |
| POST | `heartbeat` | EA health/status heartbeat |
| POST | `events` | Event ingestion; Redis pipeline use is controlled by configuration |
| POST | `telemetry` | Account, quote, and completed M1/M5/M15/H1 candle telemetry |
| GET | `market-data/status` | Per-source/timeframe candle coverage, last persisted candle, freshness, and potential intraday gaps |
| POST | `decision` | Advisory Django decision and auditable risk verdict |
| POST | `commands/next` | Poll/claim an eligible, scoped command |
| POST | `commands/ack` | Acknowledge command lifecycle and broker outcome |
| GET | `status` | Read EA heartbeat status |

The decision endpoint can call the command producer, but creation and delivery remain gated by both `RAMON_EA_COMMAND_CREATION_ENABLED` and `RAMON_EA_COMMAND_EXECUTION_ENABLED`. Both default to `0`. Configure all five `RAMON_REAL_CENT_*` identity fields locally; they default to empty. The standard risk profile caps a proposed trade at 0.5% of the account balance, using broker reported tick value and volume metadata in the account's own currency. A BUY/SELL decision alone is not permission to place an order.

## Multi-timeframe market data

The command EA sends completed M1, M5, M15 and H1 candles through the existing authenticated Django telemetry endpoint. Django validates and persists each timeframe independently in the existing `MarketBar` table; no separate candle tables are required. Repeated deliveries are idempotent, and the EA advances its in-memory candle cursor only after Django accepts the telemetry request. After an EA restart, its bounded recent history is sent again and database uniqueness prevents duplicate rows.

Use `GET /api/ea/v1/market-data/status?symbol=XAUUSD_l` with the EA bearer token to inspect row counts, oldest/latest candle timestamps, last receipt time and candidate intraday gaps per source/timeframe. Gap entries are review candidates, not definitive outages: broker maintenance and instrument-specific sessions must be considered. The initial EA history request is capped at 512 completed candles per timeframe; collection then continues incrementally. This is not a substitute for a deeper broker-history backfill.

ARIMA/SARIMA jobs read the selected `MarketBar` symbol/timeframe series; each timeframe is trained and validated independently, with M15 remaining the default training timeframe. Do not combine M1, M5, M15 and H1 candles into one frequency-mixed series. `LearningTradeOutcome` remains the dataset for the six trade-outcome learning pipelines, not for ARIMA fitting. Forecast results remain research-only and do not authorize orders.

### Candle-history retention

The recommended retention horizon is 2 years for M1, 3 years for M5, and 5 years for M15/H1. The `prune_market_bars` management command is dry-run by default and reports expired rows without deleting anything. Review the counts first; only pass `--apply` when you intentionally want to prune data. You can scope it to a source with `--source`.

```bash
docker compose exec django python manage.py prune_market_bars --symbol XAUUSD_l
docker compose exec django python manage.py prune_market_bars --symbol XAUUSD_l --source ramon-command-ea-1 --apply
```

## Safety and validation

1. Keep `RAMON_EA_COMMAND_CREATION_ENABLED=0` and `RAMON_EA_COMMAND_EXECUTION_ENABLED=0` until the real cent account scope and broker contract have been verified.
2. Verify the command EA is attached once, and keep both Django command gates disabled while validating telemetry and decisions.
3. Run on the intended real cent `XAUUSD_l` M15 chart after verifying the exact MT5 account and server.
4. Do not run a legacy `Ramon_v0.62.x` strategy EA beside the command EA. This legacy source is excluded from the update script and could duplicate order execution.
5. A Django `WAIT`, stale/missing account data, missing quote, or invalid authorization must remain fail-closed.
6. Never delete Docker volumes to silence Compose warnings.

Useful runbooks:

- [Automatic economic calendar](docs/operations/automatic-economic-calendar.md)

- [Laptop, broker, UTC and session timekeeping](docs/operations/timekeeping.md)

- [News entry lock and calendar requirements](docs/operations/news-entry-lock.md)

- [Strategy evidence, dataset export and log retention](docs/operations/strategy-data-journal.md)

- [One-command update/build](docs/operations/one-command-update-build.md)
- [EA heartbeat troubleshooting](docs/EA-HEARTBEAT-TROUBLESHOOTING.md)
- [Daily risk baseline recovery](docs/operations/daily-risk-baseline-recovery.md)
- [Real cent activation checklist](docs/operations/real-cent-activation.md)
- [Command EA v1.07 validation](docs/ramon-commandea-v1.06-validation.md)
- [Learning Engine package architecture](docs/architecture/learning-engine-package.md)
- Learning Engine dashboard: `/api/ea/v1/learning/dashboard` (linked from the main Ramon dashboard)
- [Django command protocol](docs/architecture/django-command-ea-protocol-v1.md)

## Research code vs. running service

The expanded demo profile now compares all enabled setups after entry timing,
sizing, and structural target checks. It uses H1/M5 context when available and
records candidate rankings and rejection reasons in the dashboard. See the
[professional demo playbook](docs/architecture/professional-demo-playbook.md)
for the research basis, exact rules, read-only replay command, and limitations.
The comparison score is a heuristic, not a win probability.

The dashboard also includes an [ARIMA forecast observer](docs/operations/arima-forecast-observer.md).
It forecasts the next M15 close in a separate worker, saves the prediction before
the outcome, and checks it against the actual close and an unchanged-price baseline.
Its forecasts do not influence trade decisions or order authorization.

The repository also contains SignalFlow, forecasting, learning, and historical research code. Optional research dependencies in `pyproject.toml` are not the same as the current Compose runtime. Do not assume a Chronos model service, `scripts/setup_local.sh`, `scripts/mt5_container.sh`, or an optional `mt5` Compose profile exists in the current checkout; use the actual tracked files and current runbooks.

## What counts as verified

A healthy Django container verifies the API process is responsive; it does **not** verify MT5 compilation, live telemetry, strategy quality, safe broker execution, or profitability. EA build acceptance requires a fresh MetaEditor log with `0 errors, 0 warnings`, a new nonempty EX5, and local Experts-log validation. Trading acceptance requires broker-side validation on the configured account and must not be inferred from compilation or CI alone.


### Import external Dukascopy XAUUSD history (research only)

The historical simulator now supports **Dukascopy XAUUSD BID M15**, stored
separately as `source=dukascopy-xauusd-bid-m15` and `symbol=XAUUSD`. It does
not modify `ramon-command-ea-1` broker candles. The importer downloads
official-style compressed daily BID **M1** candle files, validates the UTC
timestamps and OHLC, and aggregates only complete 15-minute groups. Missing
M1 minutes are never interpolated, and missing days are not fabricated.

To import roughly the period used for recent Raman historical tests:

```bash
docker compose exec django python manage.py import_dukascopy_xauusd \
  --start 2026-09-22 --end 2026-11-22
```

A dry run downloads and validates without inserting rows:

```bash
docker compose exec django python manage.py import_dukascopy_xauusd \
  --start 2026-10-26 --end 2026-10-30 --dry-run
```

The command is idempotent for identical OHLC values and refuses conflicting
existing bars. It accepts up to 100 UTC calendar days per invocation. Run
successive smaller ranges to obtain longer history. A real internet connection
from the `django` container and Dukascopy feed access are required. HTTP 403
or network errors are reported; no fake prices are inserted.

In the simulator, select **Dukascopy XAUUSD BID · imported history** from the
Market data dropdown, then click Load history or Run Strategy Backtest.
This imports BID prices, *not historical bid/ask spread or LiteFinance's own
execution terms*. Enter and verify the broker-specific contract/fee assumptions.
Never interpret the results as realized or broker-validated profit.
