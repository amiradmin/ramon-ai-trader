# Ramon: frozen-policy yearly validation (research only)

This command never creates EA commands, changes thresholds, fits models, or touches
broker positions. Its purpose is to describe a fixed **standard** strategy policy
under realistic *explicitly supplied* assumptions, split by calendar year.

## Preconditions

- Complete the P0 ingestion and timestamp audit, recompile the installed MT5 EA
  if relevant, and confirm runtime/replay policy parity.
- Use only DEMO broker metadata. A source-scoped heartbeat must advertise
  `account_trade_mode: 0` and valid symbol contract details.
- Import historical M15, M5 and H1 completed candles under one authenticated
  source and symbol into PostgreSQL/TimescaleDB. No automatic retrieval of the
  separately held LONA 2022–2026 dataset has been implemented.
- The importer must preserve actual candle opens in UTC and their original
  source. Do not fix the legacy off-grid history by indiscriminately rounding.
- Keep `RAMON_EA_COMMAND_CREATION_ENABLED=0` and
  `RAMON_EA_COMMAND_EXECUTION_ENABLED=0` during research.
- Record historical broker spread, commission, and slippage where possible.
  Constant costs below are illustrative *only*.

## Start with the read-only data audit

```bash
docker compose exec -T django python manage.py audit_trading_readiness \
  --source YOUR_EA_INSTANCE_ID --symbol XAUUSD_o --limit 500000 --strict
```

This covers only the most recent N bars across timeframes. For a multi-year
dataset, use enough history or inspect narrower source imports individually.

## Run the annual evaluation

```bash
docker compose exec -T django python manage.py evaluate_walk_forward_years \
  --source YOUR_EA_INSTANCE_ID --symbol XAUUSD_o \
  --first-year 2022 --last-year 2026 \
  --spread-points 22 --slippage-points 5 --commission-points 5 \
  --holding-bars 24 > ramon-yearly-validation.json
```

Results contain frozen policy SHA-256, immutable-input SHA-256 per timeframe and
year, year-local holdout counts, trades, skip reasons, and deterministic R summaries
by setup/session. Sessions are DST-aware London (08–17 local), New York (08–17
local), their overlap, and other. For fewer than 30 closed trades, no confidence
interval is reported. At 30 or more trades, a seeded **IID** bootstrap 95%
interval is shown only as a descriptive measure: it does not account for
time-series dependence, correlated setups, multiple tests, data snooping, or
policy selection.

### Interpret the results carefully

- The command does **not** optimize or train on previous years. It evaluates the
  same already-frozen standard policy on each year. Calling this a temporal
  holdout does **not** guarantee genuine out-of-sample evidence if those years
  influenced the policy.
- Each year includes only historical prior candles for warm-up, evaluates
  entries inside that year, and resolves positions using candles before its end.
- Yearly paper balances reset rather than being chained into a live equity
  curve. Missing history is marked `INSUFFICIENT_HISTORY`, not a successful
  zero-trade study.
- Fixed-cost M15 OHLC replay is not a realistic tick-level broker fill model:
  swap, changing spread, slippage tails, and intra-bar price path are absent.
  Stop-first resolution remains conservative where both levels touch.
- An interval above zero in a selected subset is not sufficient to authorize
  trading. Pre-register candidate setups, evaluate a truly untouched later
  period, and forward-test on demo before granting eligibility.
- Do not treat a passing CI test as proof that the remote/local DB contains
  clean 2022–2026 candles.

## Check test and deployment state

```bash
git status --short
git pull --ff-only origin main
docker compose up -d --build django ingest-worker
docker compose exec django python manage.py check
docker compose exec -T django python manage.py test \
  ea_bridge.tests_walk_forward \
  ramon_pipeline.signalflow.tests.test_replay --keepdb --noinput
```

If a local checkout has uncommitted changes, **preserve and review them before
pulling**; do not discard or reset a production deployment. `--keepdb`
reuses the Django test database (not the production database). Run tests only
when no other test runner is using that test database.
