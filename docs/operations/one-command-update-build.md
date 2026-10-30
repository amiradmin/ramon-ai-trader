# One-command Ramon update and build

From the existing local clone, run:

```bash
cd ~/Documents/Presentation/ramon-ai-trader-bridge && bash scripts/update_build_ramon.sh
```

This is the single deployment command for the supported local stack. It:

1. Requires a clean checkout on the configured branch (default `main`) and fast-forwards it from `origin`.
2. Validates the single `docker-compose.yaml` and builds/starts Django, the Redis Streams worker, Redis, and TimescaleDB.
3. Applies forward Django migrations and runs `manage.py check`.
4. Waits until Django, Redis, and TimescaleDB report healthy, the worker is running, Django's `/healthz` responds, Redis returns `PONG`, and PostgreSQL accepts connections.
5. Backs up the currently installed command EA files, compiles `mt5/RamonCommandEA_v1.02.mq5`, requires MetaEditor's exact `Result: 0 errors, 0 warnings` summary, and verifies a new non-empty `.ex5`.
6. Installs only `RamonCommandEA_v1.02.mq5` and `RamonCommandEA_v1.02.ex5` in the configured terminal's `MQL5/Experts` directory.

The legacy `Ramon_v0.62.x` strategy EA is deliberately not installed by this command. Do not attach it alongside the command EA.

## Prerequisites

- Existing clone at `~/Documents/Presentation/ramon-ai-trader-bridge` (or set `RAMON_REPO_DIR`).
- Clean Git working tree on the configured branch; the default is `main`.
- Existing local `.env` containing the required PostgreSQL password and Django/EA secrets. The script never creates, prints, or commits secrets.
- Docker Engine and Docker Compose plugin, Git, curl, Wine, `winepath`, `timeout`, `tr`, `grep`, `find`, `cp`, and MetaEditor.
- Default Wine prefix `~/.mt5` and terminal ID `D0E8209F77C8CF37AD8BF550E51FF075`.

Optional overrides:

```bash
RAMON_REPO_DIR="$HOME/Documents/Presentation/ramon-ai-trader-bridge" \
RAMON_GIT_BRANCH=main \
RAMON_MT5_PREFIX="$HOME/.mt5" \
RAMON_MT5_TERMINAL_ID="D0E8209F77C8CF37AD8BF550E51FF075" \
bash scripts/update_build_ramon.sh
```

Use `RAMON_METAEDITOR` if MetaEditor is installed in a nonstandard location, and `RAMON_HEALTH_URL` only if the health endpoint intentionally differs.

## After the command

The command starts the backend and installs the compiled EA, but intentionally does not launch MT5, attach an EA to a chart, or change trading permissions.

For telemetry/decision validation, open MT5, attach `RamonCommandEA_v1.02` to the intended demo `XAUUSD_o` M15 chart, set the local Django bearer token in the EA Inputs, and allow WebRequest to `http://127.0.0.1:8017`. Keep `EnableCommandExecution=false` and server-side command creation/execution disabled. Verify heartbeat, telemetry, and decision logs before any separate demo-execution review.

## Failure and safety behavior

- Stops at the first failed prerequisite, migration, Django check, health/readiness check, compiler result, or missing output binary.
- Copies existing command EA source/binary files to `~/.local/share/ramon-ea-backups/<timestamp>/` before replacement.
- Never runs `docker compose down`, removes volumes, resets the database, changes `.env` execution flags, launches MT5, attaches EAs to charts, or places trades.
- A successful deployment verifies service readiness and compilation only; it does not prove strategy quality, broker execution, demo readiness, or profitability.
