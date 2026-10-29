# Ramon Django Command Protocol v1

## Status

The Django command model and authenticated `commands/next` and `commands/ack` endpoints are implemented in the current `main` branch. API tests are being added separately. No endpoint currently creates trade commands from strategy decisions, and the existing production Ramon EA does not poll or execute this command protocol. The prototype EA is telemetry-only and has not been verified by MetaEditor compilation or demo-account execution.

This protocol is not authorization to trade. Keep `RAMON_EA_COMMAND_EXECUTION_ENABLED=0`, keep EA execution disabled, and do not create executable commands until the risk-verdict producer, EA-side validation, broker-result reconciliation, and demo tests are complete.

## Goals

- Separate MT5 telemetry collection from strategy decisions and order execution.
- Keep Django responsible for strategy evaluation and generating explicit, auditable command intents.
- Keep an independent execution guard inside MT5.
- Make command delivery retryable without treating a network timeout as proof that a broker action failed.

## Existing integration

The current bridge exposes authenticated handshake, heartbeat, events, and decision endpoints under /api/ea/v1/. Preserve backward compatibility. Inspect the existing router and auth implementation before adding routes; reuse compatible endpoints rather than duplicating them.

## Proposed API surface

| Method | Endpoint | Purpose |
| --- | --- | --- |
| GET | /api/ea/v1/handshake | Negotiate protocol and report authorization state (existing endpoint) |
| POST | /api/ea/v1/heartbeat | Report instance health (existing endpoint) |
| POST | /api/ea/v1/events | Ingest telemetry and execution events (existing endpoint) |
| POST | /api/ea/v1/decision | Request an advisory strategy decision (existing endpoint) |
| POST | /api/ea/v1/commands/next | Claim the next eligible command for this EA instance |
| POST | /api/ea/v1/commands/ack | Record command lifecycle status and broker result |

All command endpoints require the existing bearer-token authentication, bounded JSON request bodies, and validation of instance/account scope. Do not put credentials in telemetry or logs.

## Telemetry envelope

A telemetry message should contain:

- protocol_version, instance_id, account login identifier, server name and EA version.
- Symbol and timeframe.
- Server timestamp and quote timestamp, bid, ask, point size, digits and spread.
- Completed OHLC bars with Unix timestamps and an explicit closed-bar indicator.
- Account currency, balance, equity, free margin and margin level.
- Open positions with ticket, symbol, direction, volume, entry price, SL, TP and current profit.
- Last successfully processed command ID and EA health status.

Use event IDs for deduplication. Validate OHLC invariants (high >= open/close/low, low <= open/close/high), positive prices and timezone-aware timestamps. Never treat an incomplete candle as a completed bar.

## Command envelope

Every command has a unique immutable command_id, protocol version, target instance_id, account scope, symbol, action, created_at, expires_at, decision reference, risk-verdict reference, and an explicit authorization field. Unknown fields may be ignored only when doing so cannot change execution semantics.

Supported actions:

- NOOP: no trading action.
- OPEN: requires BUY/SELL direction, volume, stop-loss and take-profit, plus a strategy decision and passing risk verdict.
- CLOSE: targets a specific position ticket; partial close, if supported, must specify volume explicitly.
- MODIFY_SLTP: targets a specific position ticket and specifies the desired stop-loss/take-profit values.

A directional strategy result (BUY/SELL) is not itself an executable command. Django may create an executable command only after all mandatory strategy, entry-timing and risk checks pass. A missing risk input must not silently become approval.

## Execution state machine

1. Django creates a command only after all required checks pass; otherwise it records a rejected advisory decision.
2. The command is persisted before delivery, scoped to one EA instance/account, and given a short, explicit expiry.
3. The EA claims the command and checks schema, authorization, target account, expiry, duplicate ID and supported action.
4. Before execution, the EA rechecks current quote freshness, spread, symbol volume rules, SL/TP direction and distance, account constraints, position limits, terminal permissions and local risk caps.
5. The EA persists the command ID before attempting a broker operation.
6. The EA acknowledges a confirmed broker outcome with order/deal/position identifiers where available.
7. If the broker outcome is uncertain (for example, a timeout or lost acknowledgement), mark RECONCILIATION_REQUIRED, inspect broker orders/deals/positions and do not blindly retry an OPEN.

Suggested lifecycle states: PENDING, CLAIMED, RECEIVED, EXECUTING, EXECUTED, REJECTED, FAILED, EXPIRED, RECONCILIATION_REQUIRED.

Delivery is at-least-once with idempotency and broker reconciliation; do not promise exactly-once broker execution.

## Mandatory safety invariants

1. Telemetry and advisory decisions never place orders.
2. Execution is disabled by default in both Django configuration and EA inputs.
3. No command can bypass EA-side validation.
4. Invalid, expired, unauthorized, duplicate, or account-mismatched commands fail closed.
5. The same command ID must never cause a second broker action.
6. A missing or stale quote blocks new entries.
7. A failed Django connection blocks new command-driven entries, but does not disable local protection or management of existing positions.
8. Every decision, risk verdict, command, acknowledgement and broker result is auditable.
9. Close/modify commands are position-scoped and must not unexpectedly affect unrelated positions.
10. Live trading remains disabled until a separate, explicit authorization and demo-validation milestone is completed.

## Delivery milestones

1. Implement and test the command contract and durable persistence in Django.
2. Build the new EA in telemetry-only mode and validate telemetry against MT5.
3. Test decisions and all risk gates without executing orders.
4. Test command claim/acknowledgement and recovery with simulated execution.
5. Validate on a demo account, including duplicate delivery, EA restart, network loss, stale commands and uncertain broker outcomes.
6. Consider any broader execution authorization only after the above gates pass.
