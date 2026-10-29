# Ramon Django Command Protocol v1

## Current implementation status

The current implementation in `main` includes authenticated heartbeat/event/telemetry ingestion, an advisory decision endpoint, an auditable `RiskVerdictRecord`, a guarded strategy-to-command producer, and account-scoped command polling/acknowledgement. The decision endpoint calls `create_open_command()` when the advisory risk verdict is approved; command creation still returns without creating a command unless **both** `RAMON_EA_COMMAND_CREATION_ENABLED` and `RAMON_EA_COMMAND_EXECUTION_ENABLED` are explicitly enabled.

Both settings default to `0`, and the EA input `EnableCommandExecution` defaults to `false`. Keep all gates disabled during telemetry/decision validation. Static tests and a healthy Django endpoint do not prove safe broker execution.

## Endpoints

All API paths are relative to `/api/ea/v1/` and use the configured bearer token, bounded JSON request bodies, and validation appropriate to the endpoint.

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `handshake` | Negotiate/report protocol; trading authorization is always false |
| POST | `heartbeat` | Report EA health/status |
| POST | `events` | Ingest events; Redis buffering is controlled by `RAMON_PIPELINE_INGEST_ENABLED` |
| POST | `telemetry` | Validate and persist account, quote, and completed-bar telemetry |
| POST | `decision` | Generate an advisory strategy decision and persist a risk verdict |
| POST | `commands/next` | Claim an eligible command scoped to EA instance/account |
| POST | `commands/ack` | Record command lifecycle and broker result |
| GET | `status` | Read recent heartbeat state |

The decision endpoint is not an order-placement endpoint. A directional BUY/SELL result is advisory until all server-side command gates and account/risk checks pass; the EA must also enforce its local guards.

## Command envelope and lifecycle

Each durable command has a unique `command_id`, `instance_id`, account login, symbol, action, payload, decision reference, risk-verdict reference, expiry, and explicit `execution_authorized` flag.

Supported actions: `NOOP`, `OPEN`, `CLOSE`, and `MODIFY_SLTP`.

- `OPEN` requires direction, volume, stop-loss, and take-profit.
- `CLOSE` must target a specific broker position ticket.
- `MODIFY_SLTP` must target a specific position and provide the requested stop/target values.
- Unknown, malformed, expired, unauthorized, or account-mismatched commands must fail closed.

Lifecycle: `PENDING → CLAIMED → RECEIVED → EXECUTING → EXECUTED`.

Terminal alternatives include `REJECTED`, `FAILED`, `EXPIRED`, and `RECONCILIATION_REQUIRED`. Repeated identical terminal acknowledgements are idempotent; conflicting terminal acknowledgements are rejected. Delivery is at-least-once, not exactly-once.

## Safety invariants

1. Strategy and risk decisions remain in Django; the EA is a bridge and guarded broker executor.
2. Execution is disabled unless both Django gates and the local EA input are explicitly enabled.
3. Django command creation rechecks fresh telemetry, account/server/symbol identity, demo mode, quote freshness, open positions, and daily-risk baseline before creating an OPEN command.
4. The EA must recheck account identity, quote freshness, spread, symbol volume rules, SL/TP direction and distance, terminal permissions, local risk caps, and duplicate command IDs immediately before execution.
5. The EA must not blindly retry an OPEN after a timeout or uncertain broker result; it must reconcile broker orders/deals/positions and use `RECONCILIATION_REQUIRED` when necessary.
6. A Django/network failure blocks new command-driven entries, not protective management of existing positions.
7. Every decision, risk verdict, command, acknowledgement, and broker result must be auditable.
8. A passing source guard, passing Django tests, or successful EX5 compilation does not authorize trading. Complete the demo-only activation checklist separately.

## Validation runbooks

- [Command EA v1.02 validation](../ramon-commandea-v1.02-validation.md)
- [Demo-only activation checklist](../operations/demo-trading-activation.md)
- [Daily risk baseline recovery](../operations/daily-risk-baseline-recovery.md)
