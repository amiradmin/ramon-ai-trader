# Daily opening-balance baseline recovery

Ramon's Django decision API fails closed when it cannot find an `AccountDailyBaseline`
for the exact broker account, server, and broker day. The telemetry endpoint normally
creates this record only when it observes a continuous, authenticated transition across
the broker-day rollover. If the rollover was missed, do not disable the risk gate or
copy the current balance into the opening-balance field.

## Explicit recovery

An operator may create a baseline only after verifying the **opening balance for that
broker day** against the broker's account history/statement. The account's current balance
or equity is not a substitute: it may already include today's realized P/L, deposits,
withdrawals, or other adjustments.

Run this from the Django application directory (inside the running container, the working
directory is `/app`):

```sh
python manage.py capture_daily_baseline \
  --account-login 91422263 \
  --server-name 'LiteFinance-MT5-Demo' \
  --broker-day YYYY-MM-DD \
  --opening-balance VERIFIED_OPENING_BALANCE \
  --source-instance-id ramon-command-ea-1 \
  --confirm-broker-opening-balance
```

Replace `YYYY-MM-DD` and `VERIFIED_OPENING_BALANCE` with the broker's actual day and
opening balance from trusted broker records. Do not paste the placeholders literally.
The confirmation flag is mandatory. The command validates the identity, date, and positive
finite balance, refuses future dates, and never overwrites an existing baseline. If a
baseline already exists, inspect it rather than trying to replace it.

Example invocation in this repository's Compose setup (use only after verifying the value):

```sh
docker compose exec -T django python manage.py capture_daily_baseline \
  --account-login 91422263 \
  --server-name 'LiteFinance-MT5-Demo' \
  --broker-day YYYY-MM-DD \
  --opening-balance VERIFIED_OPENING_BALANCE \
  --source-instance-id ramon-command-ea-1 \
  --confirm-broker-opening-balance
```

Afterwards, re-check the exact account/server/day record and request a fresh decision.
This command does not change trading authorization, execution settings, or any EA inputs.
Keep command execution disabled during validation; a valid baseline alone is not a
trading approval.
