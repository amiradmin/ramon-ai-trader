# PostgreSQL driver image build (phase 7)

The model image now installs optional `postgres` dependencies permanently.
The non-PyTorch pip index is configurable with build arg
`RAMON_PIP_INDEX_URL` (defaults to https://pypi.org/simple).
CPU PyTorch still uses its separate official wheel index.

## Validated isolated test

On HP-Mini, an isolated image based on the existing production image,
with `psycopg 3.3.6` installed via the ParsPack mirror, successfully:

- imported `ramon.postgres_market_shadow` after mounting current source;
- connected over TCP and authenticated to the logging PostgreSQL service;
- inserted a synthetic M15 bar, performed idempotent upsert and checked
  OHLC/spread in a **separate temporary database**;
- removed the test database afterward.

This does NOT demonstrate a full image rebuild or live trading cutover.

## Full production-equivalent image build (not Compose deployment)

Only if sufficient disk space is available, build to a separate image tag:

```bash
df -h /
docker build --network=host \\
  --build-arg RAMON_UID="$(id -u)" \\
  --build-arg RAMON_GID="$(id -g)" \\
  --build-arg RAMON_PIP_INDEX_URL=https://mirror.abrha.net/repository/pypi/simple \\
  -t ramon-postgres-full-test:local .

docker run --rm --entrypoint python ramon-postgres-full-test:local \\
  -c 'import psycopg,ramon.postgres_market_shadow; print(psycopg.__version__)'
```

This full build can consume substantial time, downloads and disk space.
Do not run `docker compose up -d --build` merely to validate the driver.
Do not put PostgreSQL credentials or DSNs in Git or build args.

## Activation gate

- SQLite remains the sole source of truth.
- Shadow M15 writes remain disabled by default.
- Before enabling, validate a full new image, service logs, explicit rollback,
  database least privilege, freshness/parity over multiple market days, and
  performance during live decision requests.
- A synchronous PostgreSQL mirror currently happens in the request's
  persistence path; verify that worst-case connection timeout and write
  latency are acceptable **before** enabling on the live trading service.
- Never remove SQLite as part of the driver image change.
