# Automatic economic calendar

`news-worker` (`ramon-news-worker`) refreshes Forex Factory's public weekly JSON export every 15 minutes. It is a schedule feed, not a newswire or release-results API. No paid account, API key or connector session is needed. It runs without broker-order permission or a production database connection, and publishes the validated schedule atomically to the shared media volume.

Sources:

- [Forex Factory calendar and weekly exports](https://www.forexfactory.com/calendar)
- [Public weekly JSON export](https://nfs.faireconomy.media/ff_calendar_thisweek.json)

The export uses `USD` as a currency filter; Ramon maps those entries to its US-event scope and retains High/Medium impact classifications. Each event must carry an explicit offset. UTC conversion uses that offset, not the laptop or broker timezone. Forecast and previous values are retained as provider strings; no actual release values or surprise scores are invented. Published times are approximate and may be revised. Unexpected announcements, all news headlines, guaranteed completeness and exact broker fills are outside this integration.

## Start and monitor

```bash
docker compose up -d --build --wait --wait-timeout 180
docker compose logs --since 1h news-worker
docker compose ps news-worker
```

Compose configures `RAMON_NEWS_FEED_AUTOMATIC=1` in the application services. That flag alone cannot clear the gate: a fresh verified-offset ForexFactory automatic snapshot is required. A manual TipRanks import cannot clear it. The current calendar state and per-engine lock reasons are exposed in `/api/ea/v1/research/economic-calendar` and the dashboard.

The worker's health check requires a successful refresh within 30 minutes. The entry gate blocks after one hour without a successful snapshot, or immediately when its exact weekly coverage ends. A restart may reuse a snapshot less than 15 minutes old until its next refresh is due; it preserves the original receipt time. HTTP failures, malformed/oversized responses, a proxy cache older than one hour, missing offsets, wrong-week data, an empty US scope or a major event at tentative midnight do not advance the last-success timestamp. Retries are 15 minutes apart. Last good data remains available and ages normally. Reported CDN cache age also counts against the one-hour entry freshness limit; a download cannot reset an old cached response to fresh.

The full source payload, exact retrieval time and source hash are retained in `MEDIA_ROOT/research/economic-calendar.json`. Each successful import is also archived in `research/economic-calendar-archive/<snapshot-sha256>.json` for research. Include this directory in backups; no automatic history deletion is performed.

## News policy

The [entry lock](news-entry-lock.md) applies independently at creation and delivery. CPI, NFP/payrolls, PCE, Fed rate decisions/FOMC statements and press conferences trigger the configured windows. Ordinary FOMC member speeches and meeting minutes do not trigger these initial windows. Existing quote/spread/risk protections remain enforced. Blocked queued entries are rejected, and later entries require normal evaluation. Closing positions and stop/target management remain enabled.

These checks verify integration behavior, not trading profitability. Do not treat weekly forecast strings as actual economic outcomes when building model labels.
