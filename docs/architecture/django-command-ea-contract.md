# Ramon Django Command Protocol v1

## Purpose
A dedicated MetaTrader 5 EA will upload market/account telemetry and execute only explicitly authorized, risk-approved commands. The existing Ramon EA remains unchanged during development.

## Endpoints
All endpoints use the configured bearer token and bounded JSON request bodies.

| Method | Endpoint | Purpose |
|---|---|---|
| POST | `/api/ea/v1/handshake` | Negotiate protocol and capabilities |
| POST | `/api/ea/v1/telemetry` | Planned: upload completed candles, quotes, account and positions |
| POST | `/api/ea/v1/decision` | Request a strategy decision |
| POST | `/api/ea/v1/commands/next` | Claim the next eligible command |
| POST | `/api/ea/v1/commands/ack` | Report command status and broker result |
| POST | `/api/ea/v1/heartbeat` | Report EA health |

The command polling and acknowledgement endpoints are the first implementation slice. Telemetry ingestion and command generation from the complete strategy/risk approval path remain separate follow-up work. No public endpoint in this slice creates a trade command.

## Command envelope
Every command has a unique immutable `command_id`, `instance_id`, `account_login`, symbol, action, payload, decision ID, risk-verdict ID, creation time, expiry time and an explicit `execution_authorized` flag.

Actions: `NOOP`, `OPEN`, `CLOSE`, `MODIFY_SLTP`.

- `OPEN` must specify direction, volume, stop-loss and take-profit.
- `CLOSE` must target one specific broker position ticket.
- `MODIFY_SLTP` must target one specific position and provide the requested stop/target values.
- Unknown, malformed, expired, wrong-account and unauthorized commands fail closed.

## Lifecycle
`PENDING → CLAIMED → RECEIVED → EXECUTING → EXECUTED`

Alternative terminal states are `REJECTED`, `FAILED`, `EXPIRED` and `RECONCILIATION_REQUIRED`. Repeated identical terminal acknowledgements are idempotent. Conflicting terminal acknowledgements are rejected.

Delivery is at-least-once, not exactly-once. The EA must persist command IDs and reconcile uncertain broker outcomes before retrying an OPEN action.

## Safety invariants
1. Execution is disabled unless `RAMON_EA_COMMAND_EXECUTION_ENABLED` is explicitly enabled.
2. The current implementation does not create trade commands and does not enable trading.
3. Django must only enqueue executable commands after strategy and risk approval.
4. The EA must independently check account identity, fresh quotes, spread, volume, SL/TP, position limits and risk immediately before execution.
5. A Django/network failure blocks new command-driven entries, not protective management of existing positions.
6. All decisions, commands, acknowledgements and broker results must be auditable.
