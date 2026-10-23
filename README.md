# Ramon — Django EA Bridge

Ramon is being consolidated around Django as the single service-facing entry point for the MetaTrader 5 Expert Advisor (EA). The active Compose stack contains Django, Redis, TimescaleDB, and a stream worker. The legacy standalone Chronos/advisor/MT5 container Dockerfiles have been removed.

## Current status

**The migration is not yet complete.** Django currently provides health and authenticated EA telemetry endpoints. The new `ramon_pipeline/signalflow/` package defines opportunity detection, entry timing, risk sizing, and outcome classification, but it is not yet wired into a Django `/decision` endpoint.

The EA source `mt5/Ramon.mq5` still defaults to `http://127.0.0.1:8012/decision`. Do not assume the EA is using Django or enable live trading on the strength of Django's health check. Before routing decisions through Django, implement and test the decision contract, update the EA URL, and verify fail-closed behavior and EA-local risk protections.

## Active architecture

- **Django** — authenticated EA API; host port `127.0.0.1:8017`, container port `8016`.
- **SignalFlow** — modular decision stages under `backend/django_bridge/ramon_pipeline/signalflow/`; currently not connected to the EA decision route.
- **Redis** — stream buffering for opt-in event ingestion.
- **TimescaleDB/PostgreSQL** — durable telemetry and pipeline data.
- **Worker** — consumes queued events. Compose sets `RAMON_PIPELINE_INGEST_ENABLED=0` by default, so confirm the desired ingestion mode before changing it.
- **MT5 EA** — remains responsible for its own order execution and local safety checks.

## Run locally

Create or update an untracked `.env` file with these values. Use strong, unique secrets; never commit the file.

```dotenv
POSTGRES_DB=ramon
POSTGRES_USER=ramon
POSTGRES_PASSWORD=replace-with-a-strong-database-password
DJANGO_SECRET_KEY=replace-with-a-long-random-secret
RAMON_EA_BEARER_TOKEN=replace-with-a-long-random-token
```

Generate secrets with Python if needed:

```bash
python -c 'import secrets; print(secrets.token_urlsafe(48))'
```

Start the stack:

```bash
docker compose config --services
docker compose up -d --build
docker compose ps
curl -fsS http://127.0.0.1:8017/healthz
```

Useful diagnostics:

```bash
docker compose logs --tail=200 django
docker compose logs --tail=200 worker
docker compose logs --tail=100 redis timescaledb
```

The health endpoint confirms that the Django process responds; it does not prove that the EA is connected or that trading decisions work.

## Django endpoints

All EA API endpoints require `Authorization: Bearer <RAMON_EA_BEARER_TOKEN>`.

- `GET /healthz` — process health only.
- `GET /api/ea/v1/handshake` — protocol handshake; trading authorization is always false.
- `POST /api/ea/v1/heartbeat` — EA instance and terminal status.
- `POST /api/ea/v1/events` — idempotent event ingestion.
- `GET /api/ea/v1/status` — recent EA heartbeat status.

There is **no active Django decision endpoint yet**. Do not point the EA's `ModelUrl` at Django until that endpoint and its request/response contract have been implemented and tested.

## Repository map

- `backend/django_bridge/ea_bridge/` — Django EA API, authentication, models, migrations, and tests.
- `backend/django_bridge/ramon_django/` — Django project settings and URL routing.
- `backend/django_bridge/ramon_pipeline/` — ingestion, buffering, persistence, worker orchestration, and legacy strategy/risk contracts still used by parts of the pipeline.
- `backend/django_bridge/ramon_pipeline/signalflow/` — new modular opportunity, timing, risk, and learning stages.
- `mt5/` — EA source and MT5 research/telemetry utilities.
- `docs/ramon-django-pipeline/` — staged architecture and implementation plan.
- `docs/` — research and historical audit material; treat documents as records of their stated snapshot, not necessarily current runtime truth.

## Data safety

`docker compose down` stops services but preserves named database and Redis volumes. Do not use `docker compose down -v` unless you explicitly intend to delete persistent data. Keep `.env`, account identifiers, tokens, and trading records private. Keep the API bound to loopback unless a separately secured deployment is deliberately configured.

## Next implementation milestone

1. Define and validate the EA `/decision` request and response schema.
2. Adapt incoming market/account data to the SignalFlow input contract.
3. Wire Django to run opportunity detection, entry timing, and risk checks; return HOLD on invalid input or internal failure.
4. Persist decision and outcome telemetry; do not claim adaptive learning until outcomes are durably stored and evaluated.
5. Add unit and integration tests, including timeouts, malformed input, risk rejection, and unavailable Redis/database.
6. Update `mt5/Ramon.mq5` to call Django on loopback and verify behavior on a demo account before any live rollout.
