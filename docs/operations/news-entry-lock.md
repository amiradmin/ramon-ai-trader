# News entry lock

Compose enables `RAMON_NEWS_LOCK_ENABLED=1` for all Python services. It pauses new broker entries, not analysis, paper observations, closing positions, or stop/target management. The single gate is evaluated independently by each strategy at command creation and again at delivery. Orders already claimed by the EA cannot be recalled by this gate. Broker SL/TP cannot guarantee a fill price through a release.

Initial demo policy, not an optimized or proven performance improvement:

| Engines | Minutes before | Minutes after |
| --- | --- | --- |
| M1/M5 scalper, RANGE_EDGE_FADE, FAILED_BREAKOUT_SWEEP, EXHAUSTION_REVERSAL | 15 | 30 |
| TREND_PULLBACK, BREAKOUT_RETEST, SESSION_OPENING_RANGE_BREAKOUT, VOLATILITY_COMPRESSION_EXPANSION | 10 | 15 |

Window endpoints are inclusive; overlapping events keep the lock active. The gate recognizes CPI/inflation-rate releases, payrolls/NFP, PCE and FOMC/rate-decision/press-conference event names. Ordinary Medium-impact releases do not trigger a timed lock. Opening-range and compression strategies also require an input candle closed after the latest completed event window before submitting another entry. Existing spread, freshness, sizing and account-risk checks remain mandatory.

## Automatic calendar

The separate `news-worker` container now refreshes Forex Factory's weekly JSON calendar every 15 minutes. It supplies verified-offset timestamps and exact weekly coverage. Compose enables automatic-feed clearance, but the gate also requires an automatic ForexFactory snapshot less than one hour old with valid coverage. Manual TipRanks snapshots cannot clear it. See [automatic economic calendar](automatic-economic-calendar.md) for startup, health, source provenance and limitations.

A stale, invalid, missing or expired-week schedule still blocks new entries. These feed-based protections do not cover unscheduled announcements. The worker shares the persistent media volume with Django and all strategy workers. Actual release results and surprise scores are not provided.

## Evidence and monitoring

The main dashboard shows per-strategy news status and the calendar panel reports how many engines are blocked. Strategy status JSON includes the full reason and active event window. Evaluations preserve the news snapshot checksum and status. Creation and delivery blocks produce durable `EAEvent` rows of type `NEWS_ENTRY_BLOCKED`, scoped by instance/account/server/symbol/setup; repeated creation blocks are limited to one row per reason per minute. Scalper execution audit also records the gate context.

Queued OPEN commands blocked at delivery become REJECTED with the news reason and snapshot details. Delivery continues through the queue to allow close/protection commands. Old blocked entries are never resurrected; later entries need a new evaluation and the usual duplicate/risk checks.

Compare later results chronologically using the recorded lock states, costs, blocked opportunities, trade counts, drawdown and return distribution. The initial policy has execution-safety tests, not a profitability validation.
