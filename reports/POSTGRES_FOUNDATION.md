# PostgreSQL research foundation (opt-in, not cutover)

This adds an optional `research-postgres` Docker Compose service under the
`postgres-migration` profile and the existing experimental research schema
from `feature/postgres-research-telemetry-20261013`.

**SQLite is still in use.** Model/EA history, collector, dashboard, training
and lab are not switched. Never delete their SQLite database yet.

## Setup
```bash
cd ~/Documents/Presentation/ramon-ai-trader
export RAMON_PG_PASSWORD='choose-a-long-random-password'
docker compose --profile postgres-migration up -d research-postgres
docker compose --profile postgres-migration ps research-postgres
docker compose --profile postgres-migration exec research-postgres \
  psql -U ramon_ingest -d ramon_research -c '\\dt'
```
Do not commit credentials into Git or command history. For long-term use,
store secrets via your OS secret manager or Docker secrets; export only into a
private session. The compose configuration deliberately supplies no default
password: the optional container refuses to initialize without one.

Schema initialization runs on **first initialization of an empty volume only**.
Use versioned SQL migrations for upgrades. Take a tested PostgreSQL backup
before applying schema changes. The database has a named persistent volume;
no host TCP port is published.

## Release gates
See `reports/POSTGRES_FULL_MIGRATION_PLAN.md`. This infrastructure
is not a complete transfer of SQLite tables. Do not enable cutover or
delete databases until *all* writer/reader paths, historical imports,
shadow parity, rollback and restore testing have passed.
