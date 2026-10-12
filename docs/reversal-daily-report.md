# Automatic daily Reversal research report

Read-only evaluation of live M1/M5 UTC freshness and historical Reversal
by UTC hour and 16-M1-bar proxy market regime.

The Docker service `reversal-daily-report` runs immediately on startup, then
daily at **07:00 UTC** (10:30 Tehran), configurable using
`RAMON_REVERSAL_REPORT_HOUR_UTC`. It writes
`data/reports/reversal/reversal-YYYY-MM-DD.json` and atomically refreshes
`data/reports/reversal/latest.json`. Reports are persisted on the host.
If source data is missing/stale, the status is INCOMPLETE_DATA. If fewer than
five distinct UTC days exist, the audit status is
INSUFFICIENT_INDEPENDENT_DAYS. It will never promote a strategy or send trades.

```bash
cd ~/Documents/Presentation/ramon-ai-trader
git pull --ff-only origin main
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -q tests/test_reversal_daily_report.py
docker compose up -d --build reversal-daily-report
docker compose logs --tail=25 reversal-daily-report
python3 -m json.tool data/reports/reversal/latest.json | head -70
```

The model image is shared with other services. Starting the report by itself
does not require restarting the MT5 EA, but Docker may rebuild a large image.
The service does NOT update the DB, EA, account balance, order queue, live
policies, or dashboard GO status. Proxy OHLC outcomes are not executable PnL.
Inspect dataset coverage across independent trading days and tick-level costs
before considering any change to execution.
