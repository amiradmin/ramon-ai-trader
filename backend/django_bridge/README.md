# Ramon EA → Redis → Django → TimescaleDB

The bridge receives read-only EA telemetry and snapshots. It does not authorize trades or change EA risk settings. The trading decision endpoint remains separate.

## Data path

1. `mt5/Ramon.mq5` sends an authenticated heartbeat every 30 seconds and a JSON `SNAPSHOT` event at the configured interval (default 15 seconds).
2. `POST /api/ea/v1/events` validates the event and publishes it to a Redis Stream. Snapshot events are routed to `ramon:telemetry:v1`.
3. The `worker` service runs `manage.py consume_ramon_streams` and consumes Redis consumer groups.
4. The worker inserts each event into `ea_bridge.EAEvent` in PostgreSQL/TimescaleDB, using the unique `event_id` for idempotency. Redis is acknowledged only after the database transaction commits.
5. Django Admin displays saved snapshots with server-side pagination and a read-only JSON payload detail page.

## Configure and start

Set secrets in the repository-root `.env` file; never commit it:

```dotenv
DJANGO_SECRET_KEY=<a-long-random-secret>
RAMON_EA_BEARER_TOKEN=<a-long-random-token>
POSTGRES_DB=ramon
POSTGRES_USER=ramon
POSTGRES_PASSWORD=<a-strong-database-password>
RAMON_PIPELINE_INGEST_ENABLED=1
```

Keep `RAMON_PIPELINE_INGEST_ENABLED=0` while validating the services. Set it to `1` when ready for the EA events endpoint to queue events through Redis. With the setting disabled, the legacy endpoint writes events directly to the database instead of using Redis.

From the repository root:

```bash
docker compose config -q
docker compose up -d --build timescaledb redis django worker
docker compose ps -a
docker compose logs --tail=100 django worker
```

The Django service is published only on host loopback at `127.0.0.1:8017`. Health check: `http://127.0.0.1:8017/healthz`. Admin: `http://127.0.0.1:8017/admin/`.

Create an admin account once:

```bash
docker compose exec django python manage.py createsuperuser
```

In MT5, add the URL used by `DjangoHeartbeatUrl` and `DjangoEventsUrl` to **Tools → Options → Expert Advisors → Allow WebRequest for listed URL**. Set `EnableDjangoBridge=true` and the same `DjangoBearerToken` locally in the EA inputs. Do not commit or share the token. `DjangoSnapshotIntervalSeconds` controls the snapshot cadence; snapshots are observational only and never grant trading permission.

## Admin snapshot viewer

Open **EA bridge → EA events**. The list is newest-first, paginated at 50 rows per page, filterable by event type/date and searchable by instance/event ID. Select a row to inspect its full read-only JSON payload. The heartbeat list is available separately.

## Event API

- `GET /api/ea/v1/handshake` — authenticated protocol handshake; `trading_authorization` is always false.
- `POST /api/ea/v1/heartbeat` — updates current EA connectivity status.
- `POST /api/ea/v1/events` — accepts event objects containing `instance_id`, `event_id`, and `event_type`; Redis mode acknowledges only after Redis accepts the event.
- `GET /healthz` — Django process health only, not proof that the EA is connected.

All timestamps stored by Django use UTC. The EA's `observed_utc_epoch` field is retained in the JSON snapshot payload, while `received_at` records the database insertion time.

## Safety / rollout

- The bridge defaults to disabled in the EA and the Redis ingestion flag defaults to disabled.
- Keep the admin service loopback-only and use a strong admin password and bearer token.
- A Redis outage in Redis-ingestion mode returns HTTP 503 so the EA can retry; the event is not falsely acknowledged.
- The worker commits to the database before Redis `XACK`. Duplicate `event_id` values are safe to redeliver.
- Validate service health, admin login, Redis stream flow, and persisted rows before enabling regular snapshots. Never enable live trading because the telemetry bridge is healthy.
