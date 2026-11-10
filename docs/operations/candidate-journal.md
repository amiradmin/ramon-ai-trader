# Forward opportunity journal and H1 continuity

The demo decision endpoint records each candidate immediately after saving its
decision. `CandidateObservation` is stored in PostgreSQL. Snapshots include source,
symbol, signal candle, setup, direction, selection/eligibility/rejection group,
stage reasons, rank components, planned levels if present, context and the quote's
spread. The first snapshot for a source/symbol/candle/setup/policy version wins;
retries do not overwrite it. Existing historical decisions are not backfilled.

`candidate-worker` runs `manage.py run_candidate_observer` every 30 seconds.
The GET endpoint `/api/ea/v1/research/candidates` and dashboard are read-only.
They do not settle observations, change strategy weights or authorize orders.
The worker is independent of forecasting, learning and broker command workers.

## What the outcome means

Only decisions saved within two minutes after their aligned M15 signal candle
closed are eligible. Start at the **next full M15 boundary after recording**:
an earlier opening price from the already-forming candle is never treated as a
forward entry. Observe the next four exact M15 candles from the same source and
symbol, then compare the first bid open with the fourth bid close. BUY movement
is close minus open; SELL movement is open minus close. Subtract the recorded
decision-time spread once, then divide by the symbol point size.

This is **hypothetical directional price movement**, not broker profit, a reward/R
estimate, an executable rejected order, or an SL/TP backtest. Commission, slippage
and swap are excluded. Bid-side favorable/adverse excursions are descriptive
bar extremes. Positions can overlap in this journal because candidates are
independent observations, not a tradable portfolio.

Candidates without BUY/SELL directions are saved as `SKIPPED/NO_DIRECTIONAL_SETUP`;
no missing direction, stop or target is invented. A rejected directional setup can
be scored, but eligibility rejection can still be correct for reasons unrelated
to the later price direction. The final account-level action/reason is preserved
alongside the individual candidate's eligibility.

Outcomes require four exact, valid, completed candles already received by the
settlement time. Missing/off-grid rows are not interpolated or substituted. After
two hours beyond the evaluation end, an incomplete window is marked skipped.
Scored outcomes are immutable even if stored candles are later revised. Multiple
workers lock and recheck each row; the result is settled once. The dashboard
compares setup and decision groups over at most the latest 3,500 candidates for
the newest demo source/symbol. It shows sample counts and mean points, not a win
probability or a recommendation to loosen gates.

## The H1 gap observed on 9 November

The live H1 history was current, but the recent 20-bar window crossed from
Friday 6 November 21:00 UTC to Sunday 8 November 23:00 UTC. The previous rule
classified any interval above eight hours as `GAPPED`, keeping H1 neutral well
into Monday despite current hourly candles arriving normally.

The checker now recognizes a narrowly defined weekend shape: the previous candle
closes Friday 21:00–23:59 UTC; the next candle opens Sunday 21:00–23:59 UTC; the
missing wall-clock interval is 46–50 hours. It keeps observed candles without
filling the break and labels `OBSERVED_WEEKEND_BREAK`, with
`broker_calendar_verified=false`. This is a timestamp-pattern classification,
not a verified broker session calendar. Weekday outages, missing early Friday
hours, delayed Monday reopening, invalid cadence, stale or incomplete context
still remain explicit failures/neutral context. Holidays require separate
calendar evidence; they are not automatically excused.

The forward-journal panel shows current scoped H1/M5 context. The Opportunity
comparison table continues to show the context saved with its original decision,
including the weekend-break label. Prior decision records are not rewritten.

## Deploy and verify

Run migrations, then start `django` and `candidate-worker` using the normal Docker
Compose build/update. Both services use PostgreSQL/TimescaleDB. The initial panel
waits for a new decision; the first directional observations require a complete
future hour before results appear. Use `manage.py run_candidate_observer --once`
to settle currently due observations without running a permanent worker.

## Verification on 9 November 2026

All 409 Django/SignalFlow tests passed on an isolated PostgreSQL 17/Timescale
database. Coverage includes the API journal hook, immutable/idempotent snapshots,
directional rejected candidates, missing/off-grid and other-source candles,
late receipts, forward-only timing, GET isolation and weekend/weekday continuity.
Migration consistency, Python/JavaScript/shell syntax and Compose validation
passed. The disposable test database was removed afterwards.

The local PostgreSQL migration was applied, Django was confirmed healthy, and
`candidate-worker` was started. The live context endpoint returned H1 AVAILABLE
with an observed weekend-break label and M5 AVAILABLE. Seven actual candidate
snapshots were recorded from the running EA; all had no directional setup and
were correctly left unscored. The dashboard panel was visually verified. No
historical outcomes were preloaded as forward results.
