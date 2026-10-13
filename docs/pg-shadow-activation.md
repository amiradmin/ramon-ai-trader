# Opt-in PostgreSQL M15 shadow on HP-Mini

The default `compose.yaml` and live EA configuration remain unchanged.
This override enables best-effort, non-blocking **M15 shadow recording**;
SQLite stays authoritative for decisions, dashboards, training and recovery.
Do not delete SQLite.

The password exists only locally in
`.secrets/postgres-password` (mode 600) and must NOT be committed or sent
to chat. The launcher constructs the connection string inside the model
container. A Docker administrator can still inspect process environments.

## Before activation

```bash
cd ~/Documents/Presentation/ramon-ai-trader
test -s .secrets/postgres-password
test -f scripts/start_model_pg_shadow.sh
sh -n scripts/start_model_pg_shadow.sh
docker image inspect ramon-ai-trader:pg-canary-working >/dev/null
docker compose -f compose.yaml -f compose.pg-shadow.yaml config --quiet
```

A model replacement briefly interrupts EA API responses and model warmup
may last minutes. Check positions and select a safe trading window.

## Activate deliberately

```bash
docker compose -f compose.yaml -f compose.pg-shadow.yaml \
  up -d --no-deps --no-build --force-recreate model
docker compose ps model
```

Wait until the model is healthy. Then inspect flags without printing the DSN:

```bash
docker exec ramon-ai-trader-model-1 python -c \
  'import os; print("Shadow:",bool(os.getenv("RAMON_PG_MARKET_SHADOW_DSN"))); print("Async:",os.getenv("RAMON_PG_SHADOW_ASYNC_ENABLED"))'
docker logs --since 15m ramon-ai-trader-model-1 2>&1 | grep -E 'M15_POSTGRES_SHADOW_(FAILED|DROPPED)' || true
```

Confirm actual candle freshness in PostgreSQL independently. No error
logs alone do not prove successful recording.

## Roll back shadow safely

To leave the current canary image and SQLite paths running but disable
the shadow, use an override with the original Python command and no
secret mounts (recreating only model). Do not run `compose down` or
`--remove-orphans`. Maintain an image rollback tag before changes.

The boot systemd service currently starts only base + logging Compose
and can recreate the model without this override if the container is
missing. Until a separate boot integration is verified, do not rely on
shadow remaining configured following manual removal or recreation.
