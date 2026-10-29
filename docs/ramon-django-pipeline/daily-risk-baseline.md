# Server-side daily risk baseline

## Purpose

The Django decision API must not trust the EA's `daily_loss_percent` as proof that the account is below its daily-loss limit. It now derives daily loss from an immutable database baseline and the latest authenticated account equity snapshot.

## Baseline lifecycle

- Telemetry must include the broker account login, broker server name, broker calendar day (`YYYY-MM-DD`), and positive account balance/equity.
- Django creates the baseline for a new broker day only when it observes a transition from the immediately preceding broker day, on the same account/server, with continuous telemetry (no more than 90 seconds between observations).
- The baseline opening balance is the last authenticated balance observed before that rollover. The unique account/server/day key prevents later telemetry from silently replacing it.
- If the terminal is offline, the market has no valid quote, the rollover is missed, identity changes, or the prior balance is invalid, Django does not guess a baseline. Decisions remain `WAIT` with `DAILY_LOSS_BASELINE_UNAVAILABLE` until a valid baseline can be recorded.

## Daily-loss calculation

`server_daily_loss_percent = max(0, (opening_balance - current_equity) / opening_balance * 100)`

This captures realized and floating account drawdown from the recorded start-of-day balance. The EA-reported daily-loss value remains required by the wire contract for compatibility, but is not used for authorization.

The existing 3% daily-loss guard in SignalFlow rejects further directional decisions when the server-derived value reaches the configured threshold. Open positions continue to fail closed until the server can independently verify their stop-loss risk from complete broker metadata.

## Safety and rollout

- Telemetry remains observational; this change does not enable command execution or grant trading authorization.
- `EnableCommandExecution` must remain `false` during validation.
- Inspect the decision response fields `daily_baseline_available`, `daily_baseline_day`, `daily_baseline_balance`, `server_daily_loss_percent`, and `daily_loss_source`.
- An unavailable baseline is an intentional safety stop, not a reason to manually override the decision.
- The baseline uses the last balance observed immediately before rollover. Deposits, withdrawals, or broker-specific balance adjustments during the day can affect a simple equity-drawdown calculation; if the account has unusual cash flows, keep execution disabled and reconcile the broker history before relying on the metric.
