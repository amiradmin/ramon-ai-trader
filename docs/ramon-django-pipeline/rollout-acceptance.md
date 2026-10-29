# Ramon rollout acceptance checklist

This checklist defines what must be true before the Django decision service and MT5 bridge can be considered operationally validated. A successful GitHub merge or database migration alone is not evidence that the bot is safe or profitable.

## Architecture invariants

- Django is the authoritative source for strategy, signal, account-risk, and decision logic.
- The MT5 EA reports authenticated telemetry and, only in a separately approved phase, executes broker-side commands returned by Django.
- Missing, stale, mismatched, or unverifiable risk inputs must fail closed to `WAIT`.
- Keep `EnableCommandExecution=false` during rollout, telemetry validation, and shadow observation. Do not set trading authorization true as a way to bypass a guard.
- Never commit broker credentials, bearer tokens, `.env` files, account identifiers, or real account telemetry.

## GitHub / source acceptance

- [ ] The main branch has a passing Django pipeline workflow for relevant backend, Compose, documentation, and workflow changes.
- [ ] `python manage.py check` passes.
- [ ] `python manage.py makemigrations --check --dry-run` passes.
- [ ] `python manage.py test --verbosity 2` passes, including baseline rollover, missed-rollover, and server-derived drawdown tests.
- [ ] Compose configuration validates using CI-only credentials.
- [ ] Review the exact merged source and the workflow run; do not treat a missing status as a pass.

## Deployment acceptance

Run these checks against the intended local Compose project, not an unrelated Docker project:

1. Confirm the Django container is healthy and the Redis and TimescaleDB containers are healthy.
2. Confirm the running image contains `AccountDailyBaseline` and migration `0004_accountdailybaseline`.
3. Confirm `showmigrations ea_bridge` marks `0004_accountdailybaseline` as applied.
4. Confirm `python manage.py migrate --plan` reports no planned migration operations.
5. Review Django logs for startup, migration, database, and worker errors.
6. Confirm the health endpoint responds locally at `http://127.0.0.1:8017/healthz`.

Example read-only commands:

```bash
docker compose ps
docker compose logs --tail=200 django worker
docker compose exec django python manage.py check
docker compose exec django python manage.py showmigrations ea_bridge
docker compose exec django python manage.py migrate --plan
curl -fsS http://127.0.0.1:8017/healthz
```

Do not publish the output of `docker compose config` in public issues or logs; resolved environment values may contain secrets.

## Daily-risk baseline acceptance

- [ ] A test account rollover creates exactly one immutable baseline for the account, server, and broker calendar day.
- [ ] Duplicate telemetry cannot overwrite an existing baseline.
- [ ] Missing or interrupted rollover telemetry leaves the baseline unavailable and returns `WAIT`.
- [ ] Stale telemetry, account/server/symbol mismatch, invalid balance/equity, and incomplete risk inputs return `WAIT`.
- [ ] Daily loss is derived from the stored opening balance and authenticated current equity, not from the EA-reported daily-loss percentage.
- [ ] An open position remains blocked while its stop-loss risk cannot be independently verified from complete broker metadata.
- [ ] Deposits, withdrawals, broker adjustments, and timezone/broker-day behavior are documented and reconciled before relying on the metric for execution.

Formula used by the decision service:

```text
server_daily_loss_percent =
  max(0, (opening_balance - current_equity) / opening_balance * 100)
```

The baseline is captured only from continuous authenticated telemetry across the broker-day rollover. If that rollover is missed, do not insert a guessed baseline or manually bypass the guard.

## MT5 bridge acceptance

- [ ] Compile the exact versioned `.mq5` source in MetaEditor and record the resulting compiler output.
- [ ] Install the compiled `.ex5` into the intended MT5 terminal's Experts directory and verify the source/binary version matches.
- [ ] Confirm the terminal allows WebRequest to the configured local Django endpoint.
- [ ] Verify authenticated telemetry includes the account login, broker server, broker day, balance, equity, and position snapshot.
- [ ] Verify the EA receives a valid, parseable decision response and logs HTTP errors clearly.
- [ ] Observe multiple valid broker-day and decision cycles in a demo account with command execution disabled.
- [ ] Confirm no orders are sent and no command-execution setting or trading authorization has been enabled.

Compilation, terminal installation, broker connectivity, and demo runtime checks require the actual MT5 environment; GitHub Actions cannot prove these steps.

## Final go/no-go rule

The rollout remains **NO-GO for automated order execution** until all source, deployment, baseline, EA compilation, and demo-observation checks above have been reviewed and explicitly accepted. A passing CI run is necessary but not sufficient. Never claim profitability without a separate, reproducible out-of-sample and forward-demo evaluation.
