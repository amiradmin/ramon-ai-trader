# H1/H4 trend candidate — paper only

Requested November 10, 2026 as a third, separate container. This candidate cannot
open broker positions. Main Ramon and fast scalping retain their existing control
and risk settings. No additional EA is needed.

## Frozen initial hypothesis

Version `h1-h4-breakout-v1`: H1 EMA20/50 and derived H4 EMA20/50 must align;
H4 slow EMA slope must agree. A completed H1 close must break the preceding
20 H1 highs/lows. Stop is 2 H1 ATR14 from the reference entry, target 3 initial R,
maximum holding period 48 consecutive H1 bars. No optimization is authorized.
These parameters are engineering hypotheses, not estimated win probabilities.

H4 is derived in UTC four-hour buckets from exactly four valid closed H1 bars.
Incomplete buckets are omitted and counts reported. Historical EMA warmup uses
observed session bars across gaps; gaps are never filled. The recent 21-bar H1
breakout/ATR window must be consecutive. Required warmup: 64 H1 and 64 derived H4.
Only the configured source/login/server/symbol is observed, on explicit demo mode.
Forward input receipts must precede recording; future/forming bars are excluded.

The observer evaluates within five minutes after a fresh H1 completion. Paper
entry is the next FULL H1 open after recording (approximately one hour after
the signal closes). Replay models the same delay; no retroactive orders exist.
One pending paper position is allowed per scope. Missing outcome H1 bars become
SKIPPED after an hour of receipt grace; weekends are not silently bridged.
First observed outcome bars are frozen; completed observations cannot be rewritten.

Costs: frozen quote spread, 5-point adverse slippage each side, 5-point round-trip
commission and a conservative assumed 50-point overnight debit for either side.
Rollover is assumed 22:00 UTC, Wednesday triple; these are NOT retrieved broker
swap terms. Entry gates reserve four debit-night units in the 20% cost/risk check.
Outcome charges include the full exit candle conservatively. Stress replay doubles
spread, slippage and overnight debit. SL/TP ambiguity is stop-first; shorts exit
at ask; stop gaps receive adverse opening fills. No account returns are claimed.

## Operation

`docker compose up -d --build django trend-worker` builds the `ramon-trend-worker`
container and dashboard API. Django applies migration 0017. The worker cycles every
15 seconds, stores its heartbeat in the database, and has a 90-second Docker
health check. All broker command gates are forcibly OFF inside this container.
`docker compose stop trend-worker` stops collection only. Existing engines keep
running. No public ports or separate broker connection are added.

Monitor `/api/ea/v1/research/trend` or the H1/H4 card on the main dashboard.
It explicitly reports paper-only mode, worker freshness, waiting reasons, pending,
skipped and scored observations, expectancy and closed cumulative-R drawdown.
Read-only status requests never create observations or commands.

Reproduce descriptive evaluation:

```bash
docker compose exec -T django python manage.py evaluate_trend \
  --source ramon-command-ea-1 --symbol XAUUSD_o \
  --spread-points 22 --slippage-points 5 --commission-points 5 \
  --overnight-points 50 --rollover-utc-hour 22
```

Reports retain source, symbol, contract, cost assumptions, policy hash, input hash,
coverage, data exclusions and persisted main thresholds. Research snapshots and
reports belong under `research/trend/`. A no-position baseline has zero trading
P&L. Historical replay is descriptive, not forward evidence. Forward results start
only after deployment; this candidate must not be tuned against that future sample.
Main/scalper comparisons require aligned evaluation periods, costs, exposure and
execution conventions before any ranking or diversification claim is justified.
No profitability or broker-activation claim follows from a positive small replay.
