# Seven independent strategy containers

The combined entry engine is retired in the deployed Compose configuration.
Each setup evaluates and trades independently, without waiting for, competing
against, or being selected over another setup. One stopped or paused worker does
not stop the other workers. The strategy implementation and risk mechanics are
shared source code; each worker has a separate process, control, evaluation stream,
command identity and broker magic number.

| Setup | Compose service | Magic |
| --- | --- | --- |
| TREND_PULLBACK | trend-pullback | 26092301 |
| BREAKOUT_RETEST | breakout-retest | 26092302 |
| FAILED_BREAKOUT_SWEEP | failed-breakout-sweep | 26092303 |
| RANGE_EDGE_FADE | range-edge-fade | 26092304 |
| SESSION_OPENING_RANGE_BREAKOUT | session-opening-range-breakout | 26092305 |
| VOLATILITY_COMPRESSION_EXPANSION | volatility-compression-expansion | 26092306 |
| EXHAUSTION_REVERSAL | exhaustion-reversal | 26092307 |

## What each container owns

Every five seconds, `run_strategy_worker` checks only its configured demo feed and
control. It evaluates completed, receipt-cutoff M15 history at most once per 30
seconds, using the existing persisted demo thresholds, entry rules, structural
stops/targets and sizing. Only its assigned setup is enabled inside its detector;
the reused single-candidate pipeline cannot choose a different setup. Existing
demo-only supplemental setup switches are preserved. No detector thresholds,
per-trade risk settings or account exposure ceiling are raised by this migration.

Decision IDs include source, login, server, symbol, bar and setup. Repeated polls
cannot create multiple commands for one setup/bar. Each strategy allows one
verified position at a time and blocks its own unsettled or uncertain entries.
Different strategies may coexist on the required hedging account. Ownership needs
both the assigned magic and an exact executed, authorized OPEN command ticket.
Unknown/manual/old monolith positions block new strategy entries until resolved;
they are never claimed by a new strategy.

Each container runs its own eligible +1R breakeven management under the existing
profit-protection gate. Pausing entries leaves this management running. Stopping
the container stops its new entries and dynamic management; broker SL/TP stay in
force. Manual close from its page targets one exact owned ticket. Failed/uncertain
closes remain visible and are not blindly retried.

## Necessary shared infrastructure

The workers share PostgreSQL, the account telemetry feed, the MT5 command bridge,
and an account exposure ceiling. Creation locks the common heartbeat row and
counts open positions, pending reservations and unobserved fills from every engine,
including the scalper. This serialization prevents concurrent workers from spending
the same risk headroom. Dependency failure (database, bridge, stale feed) blocks
orders; process independence is not account or infrastructure isolation.

Use only the existing command EA v1.09. It already accepts command magic numbers
and exact ticket operations. No additional strategy EAs or chart bridges are
attached. The existing fast scalper and H1/H4 paper candidate remain separate.
New strategy outcomes are tagged `SETUP_<name>` and excluded from the old pooled
learning sample; each strategy page shows only its own results.

## Dashboard and controls

The main dashboard has seven cards with an **Open strategy** link. Each links to
`/api/ea/v1/strategies/<service-name>` and shows process heartbeat, feed readiness,
entry state, decisions, verified positions, broker P&L and command history.
Staff authentication and CSRF protect Start/Pause/Close. Pause expires that
strategy's still-PENDING OPEN commands; a command already claimed by MT5 may
finish. It does not pause or cancel another worker's commands.

`/api/ea/v1/strategies` is a read-only fleet status API. A stale heartbeat is
shown after 90 seconds; it is not inferred from the last trading decision.
Docker health also checks the worker's own heartbeat file. Start/Pause controls
entry activity, not Docker power; start a stopped container with Compose.

```bash
docker compose up -d --build
docker compose logs --tail=50 trend-pullback
docker compose stop trend-pullback   # other setup workers continue
docker compose up -d trend-pullback
```

`RAMON_STRATEGY_CONTAINERS_ENABLED=1` in Compose disables the old public combined
decision endpoint: it returns WAIT / MONOLITH_RETIRED_USE_STRATEGY_CONTAINERS and
cannot create old combined entries. Workers call the shared evaluation function
directly with a server-owned setup identity. `RAMON_STRATEGY_INSTANCE`,
`RAMON_STRATEGY_LOGIN`, `RAMON_STRATEGY_SERVER` default to the existing configured
demo protection scope. Each scope is pinned in its control row; changing the
environment cannot silently move active controls to another account.

`worker` was only Redis persistence, not the combined strategy. It is replaced
by `ingest-worker` / `ramon-ingest-worker`, retaining the Redis consumer group and
idempotent persistence. Remove `ramon-worker` only after the replacement runs.
Historical records, shared APIs and strategy source remain available for audit
and research. No database volumes are removed. Do not run `down -v`.

## Validation

Tests cover exact one-setup selection for all seven workers, distinct order magic,
per-strategy deduplication, simultaneous independent commands, ownership, scoped
manual close, isolated pause/cancellation, shared exposure reservations and a real
two-thread PostgreSQL race for the same headroom. They also verify retirement of
the public combined entry path, seven dashboard links, CSRF/staff controls and
learning isolation. Profitability and broker fills are separate from these
engineering checks; the architecture change establishes neither.


## Local cutover verified — November 10, 2026, about 12:49 Tehran

All seven containers reported healthy with fresh independent worker heartbeats,
automatic demo entries On, and their own persisted evaluations. They were waiting
on their individual technical/risk gates; no new strategy broker commands were
observed during verification. An actual fill for these new strategy identities is
not yet verified. No forced test trade was sent.

The combined API returned retirement mode; zero pending legacy OPEN commands
needed expiration. `ramon-worker` was stopped and removed after `ingest-worker`
started. Database and Redis volumes, old history and existing positions were kept.
The scalper remains automatic at 0.150%; H1/H4 remains paper-only.

493 tests passed in an isolated PostgreSQL database, including the real concurrent
risk-reservation test. Baseline test environment explicitly set the legacy API
mode and supplied-quality default; independent-worker tests override these with
split mode. Migration parity, Compose validation, Python compilation, shell
syntax, dashboard JavaScript syntax and local shell source-guard checks passed.
The deployed fleet API, dashboard data endpoint (HTTP 200), seven visible links
and fresh per-worker states were verified. No new MT5 compilation was needed.

## Dashboard review — November 10, 2026

The main page prioritizes the seven live workers and account-level broker results.
Legacy protection and exit research are collapsed and explicitly historical.
Saved evaluation diagnostics, educational content and shared threshold editors
are grouped in a collapsed panel with a strategy scope filter over the latest
50 records. The latest snapshot and scanner rows identify the owning setup;
no single evaluation is presented as a fleet-wide final decision. Range history
remains explicitly account-scoped, independent of that filter. Scalper monitoring
now reports current broker readiness, entry control and execution gates separately
from its paper-only results. Default-off wording was removed from live status.
Browser verification confirmed the TREND_PULLBACK scope and actual scalper state;
493 PostgreSQL tests and dashboard JavaScript syntax checks passed.


## Obsolete interface cleanup — November 10, 2026

Removed the old combined-engine scanner, duplicate range summary, educational
learning mode, conceptual-variable reference, generic risk/workflow panels and
legacy protection/exit-research panel from the main dashboard, together with their
JavaScript renderers and refresh work. Active strategy diagnostics and shared
threshold controls remain. The diagnostic scope now includes only independent
strategy evaluations. History and research APIs, model training tools and stored
records remain available; existing legacy positions retain server-side protection.
Removed the unused pre-worker Compose backup and three empty root artifacts.
The authenticated MT5 compatibility endpoint remains to report retirement, and
shared evaluation/compatibility code remains covered by regression tests.
