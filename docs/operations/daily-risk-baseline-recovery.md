# Daily account baseline telemetry (informational)

## Purpose

Ramon no longer enforces a daily-loss cap and does not require an account daily baseline to evaluate or authorize a trade. Daily broker-day baselines may still be collected and retained for historical reporting, reconciliation, and analysis. Missing baseline data or a large daily drawdown must not block an otherwise valid OPEN command.

## What remains mandatory

The absence of a daily baseline does not waive per-trade or execution checks. Django must still validate fresh account telemetry, demo-account eligibility where configured, current account and symbol data, open-position risk, approved volume, stop-loss and take-profit levels, reward-to-risk, command expiry, duplicate-command protection, and broker-server identity.

## Inspecting historical baselines

The following command is optional and is intended only for observability:

```bash
docker compose exec -T django python manage.py shell -c '
from ea_bridge.models import AccountDailyBaseline
for row in AccountDailyBaseline.objects.order_by("-broker_day")[:10]:
    print(row.account_login, row.server_name, row.broker_day,
          row.opening_balance, row.capture_method, row.source_instance_id)
'
```

Do not create or edit a baseline merely to unblock trading. Baseline capture and daily P/L values are not authorization inputs.
