# Ramon Django EA Bridge (initial slice)

This service provides a small authenticated telemetry channel between the MT5 EA and Django. It does **not** authorize trades, change EA risk settings, or replace the Chronos decision endpoint (`127.0.0.1:8012/decision`). It is intentionally an optional sidecar so existing trading services are not changed by default.

## Start locally

From the repository root, create `.env` values without committing them:

```bash
printf 'DJANGO_SECRET_KEY=%s\n' "$(python -c 'import secrets; print(secrets.token_urlsafe(48))')" >> .env
printf 'RAMON_EA_BEARER_TOKEN=%s\n' "$(python -c 'import secrets; print(secrets.token_urlsafe(48))')" >> .env
docker compose -f compose.yaml -f compose.django.yaml up -d --build django-bridge
curl -fsS http://127.0.0.1:8016/healthz
```

Keep the endpoint bound to loopback unless you deliberately configure TLS and a trusted network path. Never commit `.env` or place the bearer token in source code. In MT5, add `http://127.0.0.1:8016` to Tools → Options → Expert Advisors → Allow WebRequest for listed URL. Configure the same token in the EA's `DjangoBearerToken` input. The EA input is local terminal configuration; do not export or share set files containing it.

## API contract

- `GET /api/ea/v1/handshake` — authenticated protocol/version handshake. `trading_authorization` is always `false`.
- `POST /api/ea/v1/heartbeat` — upserts `instance_id`, `account_login`, `server_name`, `symbol`, `ea_version`, `status`, `terminal_connected`, `trade_allowed`, plus the complete bounded JSON payload.
- `POST /api/ea/v1/events` — idempotently accepts event records keyed by unique `event_id`.
- `GET /healthz` — process health only; it does not prove the EA is connected.

Requests use `Authorization: Bearer <RAMON_EA_BEARER_TOKEN>`. The API rejects requests if no token is configured, limits bodies to 64 KiB, validates JSON objects, and does not expose any endpoint that can change trading parameters. Treat the account login as operationally sensitive; don't publish this dashboard/API to the internet.

## Latency / socket policy

The EA currently makes synchronous `WebRequest` calls to the model endpoint. This bridge should be called at a low frequency (heartbeat every 15–30 seconds, events only when needed), with a short timeout and outside the order-entry decision path. Record client-side elapsed milliseconds and inspect p95/p99 latency before choosing another transport. On loopback, HTTP is typically the simplest and sufficiently fast option. If measured latency or synchronous stalls are unacceptable, move telemetry to a persistent socket worker/bridge; do **not** replace the model decision call with a socket or route trade authorization through Django as part of this telemetry change.

## Safety and rollout

1. Keep the bridge disabled in the EA until the token and URL are configured.
2. Verify heartbeat freshness and event idempotency, then test service restart and disconnect/reconnect.
3. A Django outage must not stop existing-position management or weaken any EA-local risk guards. This telemetry-only API must never be treated as a trade permission.
4. The bridge is an initial integration slice, not proof of a live connection. Do not enable live trading based on `/healthz` alone.
