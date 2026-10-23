# Ramon Django EA Bridge

Django is the active EA-facing service in the current Compose stack. It exposes authenticated telemetry endpoints; it is **not yet the EA's trading-decision service** and does not authorize orders or change EA risk settings.

## Run

Create an untracked root `.env` file with strong, unique values for:

```dotenv
POSTGRES_DB=ramon
POSTGRES_USER=ramon
POSTGRES_PASSWORD=replace-with-a-strong-database-password
DJANGO_SECRET_KEY=replace-with-a-long-random-secret
RAMON_EA_BEARER_TOKEN=replace-with-a-long-random-token
```

Then run from the repository root:

```bash
docker compose config --services
docker compose up -d --build
docker compose ps
curl -fsS http://127.0.0.1:8017/healthz
```

Compose binds Django to host loopback `127.0.0.1:8017` and container port `8016`. Keep the API private unless a separate secured deployment is deliberately configured. Never commit `.env` or publish bearer tokens.

## API

All `/api/ea/v1/` endpoints require `Authorization: Bearer <RAMON_EA_BEARER_TOKEN>`.

- `GET /healthz` — process health only; it does not prove EA connectivity.
- `GET /api/ea/v1/handshake` — protocol/version handshake; `trading_authorization` is always `false`.
- `POST /api/ea/v1/heartbeat` — records EA instance and terminal status.
- `POST /api/ea/v1/events` — idempotently accepts events, either in the database or (when explicitly enabled) through Redis ingestion.
- `GET /api/ea/v1/status` — recent EA heartbeat status.

The body limit is 64 KiB. Requests are validated as JSON objects, and a missing/invalid bearer token is rejected.

## Important migration status

The EA source `mt5/Ramon.mq5` still defaults to `http://127.0.0.1:8012/decision`. Django does **not** currently expose that decision endpoint. Do not change the EA's URL or enable live trading until the decision contract is implemented, risk checks fail closed, and demo-account integration tests pass.

Telemetry calls should remain outside the order-entry path. A Django outage must not stop EA-local management of existing positions or weaken its local risk controls.
