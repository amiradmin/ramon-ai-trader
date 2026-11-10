# Reliability and immutable evaluations

Implemented November 10, 2026. This instrumentation preserves the existing
strategy, sizing thresholds and execution switches. It never retries orders or
changes their acknowledgement states.

Each successfully evaluated decision request now creates a DecisionEvaluation
with its original features, identity, action, rationale and a unique risk-verdict
link. The response includes evaluation_id. New OPEN commands retain the exact
creating evaluation; repeated evaluations retain the original command link.
The legacy StrategyDecisionRecord continues serving as a latest-view cache.
Model/queryset guards and PostgreSQL/SQLite triggers reject updates and deletes
of evaluation records. No historical evaluations are fabricated. Rejected
malformed or stale-bar API requests do not constitute strategy evaluations.

New closed-trade learning snapshots and execution metrics prefer the command's
immutable evaluation. Existing immutable outcomes are not rewritten. Legacy
commands retain unavailable evaluation links and existing measurement behavior.

GET /api/ea/v1/reliability is a local dashboard endpoint, following the existing
localhost dashboard access model. It is read-only and sends Cache-Control:
no-store. It shows heartbeat/quote freshness, independently scoped completed
M1/M5/M15/H1 candle freshness, recent evaluations, broker verification and
recorded incidents. READY describes observed feed freshness, not permission to
trade, strategy readiness or profitability. Historical holidays/session closures
are not inferred; stale candles are labelled rather than filled.

Reconciliation checks instance, account, server and symbol scope. An OPEN can
be confirmed by an exactly command-linked closed outcome or fresh post-ack
telemetry showing the ticket, side, volume and requested SL/TP. A MODIFY requires
fresh post-ack telemetry with the target ticket and matching SL/TP within half
a broker tick. Historical requested stops can differ from a current position
after a legitimate later change; POSITION_MISMATCH reports that difference,
not necessarily an execution failure. Missing positions do not prove CLOSE
success. Unsupported ticket mappings remain unverified. Reconciliation reflects
current evidence and is not a persistent broker execution certificate.

The authenticated telemetry transaction records gaps longer than 90 seconds on
recovery and quote freshness transitions. Quote staleness is measured at receipt,
so repeated stale quotes do not create repeated transition incidents. Account
or server changes are never joined into one gap. Incident collection begins at
deployment. A powered-off host cannot write incidents or notify anyone; the
current dashboard reports staleness, and the recovered gap is persisted later.
No external alerting or system power/network configuration is changed.

## Validation

Full suite: 437 tests passed using disposable PostgreSQL 17/TimescaleDB, on an
internal Docker network without production volumes or a broker route. Additional
focused final checks: 11 tests passed. The initial SQLite run exposed an existing
decimal formatting expectation in dashboard tests; the production-compatible
PostgreSQL suite passed. Dashboard JavaScript syntax and migration/model parity
were checked. Database immutability, repeated decision evaluations, evidence
freshness, scope isolation, stop mismatch, missing-position uncertainty,
read-only GET behavior and quote incident transitions have regression coverage.
Tests do not establish that a real broker stop modification succeeds; no test
order is placed by this feature.

## Deployment verification

Deployed to the local Django service on November 10 at approximately 09:49
Tehran time, with a subsequent dashboard-only refresh at 09:50. Additive migration
0013 is applied. The service reports healthy. Four genuine WAIT evaluations
were visible by 09:50:29, preserving successive 30-second evaluations of the
same signal period. Browser verification showed the current EA feed READY with
fresh M1/M5/M15/H1 candles. An old simulator feed was separately labelled stale.
No broker test orders were sent and no trading settings were changed. Disposable
test resources were removed. The dashboard preserves expanded details on refresh.
