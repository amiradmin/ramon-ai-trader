# Ramon AI Trader

Ramon is a local MT5 gold-trading project whose **Django service owns strategy decisions, risk checks, and command authorization**. The MT5 Expert Advisor (EA) is a telemetry/command bridge and broker-side executor; it must not become a second strategy engine.

> **Deployment entry point:** `scripts/update_build_ramon.sh` is the single-command local deployment. It starts and validates the Compose services and compiles/installs only `RamonCommandEA_v1.02.ex5`. GitHub Actions has verified the command EA source contract and MetaEditor compilation for the current reviewed change. Local deployment still needs to be run on the target machine.

## Current supported stack

The single `docker-compose.yaml` defines:

- `django`: Django API, bound to `127.0.0.1:8017` on the host.
- `worker`: Redis Streams consumer that persists queued events.
- `redis`: event buffer.
- `timescaledb`: PostgreSQL/TimescaleDB durable store.

Health check: `http://127.0.0.1:8017/healthz`

The Compose pipeline ingestion switch is off by default. The named database and Redis volumes contain persistent state; **never use `docker compose down -v` as a troubleshooting step**.

## Setup and update

Prerequisites: Docker Engine with the Compose plugin, Git, curl, Wine, MetaEditor from the MT5 installation, and an existing local `.env` file. The file must supply the database password and the configured Django/EA secrets. Do not commit `.env` or paste bearer tokens into logs or chat.

For the existing clone at `~/Documents/Presentation/ramon-ai-trader-bridge`:

```bash
cd ~/Documents/Presentation/ramon-ai-trader-bridge
git switch main
git pull --ff-only origin main
bash -n scripts/update_build_ramon.sh
bash scripts/update_build_ramon.sh
```

If the clone lives elsewhere, set `RAMON_REPO_DIR` to its absolute path before running the script. Optional build settings include `RAMON_MT5_PREFIX`, `RAMON_MT5_TERMINAL_ID`, `RAMON_METAEDITOR`, and `RAMON_HEALTH_URL`; see [the update/build runbook](docs/operations/one-command-update-build.md).

The script validates the Compose configuration, builds/starts services, applies forward migrations, runs Django's system check, verifies Django/Redis/TimescaleDB health and worker status, backs up the current command EA, then compiles and installs only `RamonCommandEA_v1.02.mq5`. The legacy `Ramon_v0.62.x` strategy EA is deliberately excluded to avoid installing a second EA alongside the command architecture. The script does not launch MT5, attach an EA, change trading flags, or place orders. It stops rather than accepting a missing compiler log or a stale EX5 as a successful build.

## EA and API

The versioned command EA source is `mt5/RamonCommandEA_v1.02.mq5`. It defaults to:

- `EnableCommandExecution=false`
- empty `DjangoBearerToken` (set only in MT5 Inputs on the local machine)
- command creation and execution disabled server-side unless explicitly configured otherwise

Authenticated API routes are under `/api/ea/v1/`:

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `handshake` | Protocol handshake; never grants trading authorization |
| POST | `heartbeat` | EA health/status heartbeat |
| POST | `events` | Event ingestion; Redis pipeline use is controlled by configuration |
| POST | `telemetry` | Account, quote, and completed-bar telemetry |
| POST | `decision` | Advisory Django decision and auditable risk verdict |
| POST | `commands/next` | Poll/claim an eligible, scoped command |
| POST | `commands/ack` | Acknowledge command lifecycle and broker outcome |
| GET | `status` | Read EA heartbeat status |

The decision endpoint can call the command producer, but creation and delivery remain gated by both `RAMON_EA_COMMAND_CREATION_ENABLED` and `RAMON_EA_COMMAND_EXECUTION_ENABLED`. Both default to `0`. A BUY/SELL decision alone is not permission to place an order.

## Safety and validation

1. Keep `RAMON_EA_COMMAND_CREATION_ENABLED=0` and `RAMON_EA_COMMAND_EXECUTION_ENABLED=0` until the demo-validation checklist is completed.
2. Keep `EnableCommandExecution=false` in MT5 while validating telemetry and decisions.
3. Run only on the intended demo `XAUUSD_o` M15 chart for diagnostics.
4. Do not run a legacy `Ramon_v0.62.x` strategy EA beside the command EA. Those strategy sources are not present in this repository and have not been reviewed here for duplicate order execution.
5. A Django `WAIT`, stale/missing account data, incomplete daily-risk baseline, missing quote, or invalid authorization must remain fail-closed.
6. Never delete Docker volumes to silence Compose warnings.

Useful runbooks:

- [One-command update/build](docs/operations/one-command-update-build.md)
- [EA heartbeat troubleshooting](docs/EA-HEARTBEAT-TROUBLESHOOTING.md)
- [Daily risk baseline recovery](docs/operations/daily-risk-baseline-recovery.md)
- [Demo-only activation checklist](docs/operations/demo-trading-activation.md)
- [Command EA v1.02 validation](docs/ramon-commandea-v1.02-validation.md)
- [Django command protocol](docs/architecture/django-command-ea-protocol-v1.md)

## Research code vs. running service

The repository also contains SignalFlow, forecasting, learning, and historical research code. Optional research dependencies in `pyproject.toml` are not the same as the current Compose runtime. Do not assume a Chronos model service, `scripts/setup_local.sh`, `scripts/mt5_container.sh`, or an optional `mt5` Compose profile exists in the current checkout; use the actual tracked files and current runbooks.

## What counts as verified

A healthy Django container verifies the API process is responsive; it does **not** verify MT5 compilation, live telemetry, strategy quality, safe broker execution, or profitability. EA build acceptance requires a fresh MetaEditor log with `0 errors, 0 warnings`, a new nonempty EX5, and local Experts-log validation. Trading acceptance requires a separate demo-only lifecycle test and must not be inferred from compilation or CI alone.
