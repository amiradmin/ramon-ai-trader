# One-command Ramon update and build

From the existing local clone:

```bash
cd ~/Documents/Presentation/ramon-ai-trader-bridge
bash scripts/update_build_ramon.sh
```

The script fast-forwards the selected Git branch, validates the single `docker-compose.yaml`, rebuilds and starts the Django/worker/Redis/TimescaleDB stack, applies forward migrations, runs Django checks, waits for `/healthz`, and compiles and installs both versioned EAs:

- `mt5/RamonCommandEA_v1.03.mq5` -> `RamonCommandEA_v1.03.ex5`
- `mt5/Ramon_v0.62.7.mq5` -> `Ramon_v0.62.7.ex5`

The binaries and sources are installed under the active terminal's `MQL5/Experts` directory. Existing files are copied to `~/.local/share/ramon-ea-backups/<timestamp>/` before replacement. Each build requires MetaEditor's exact `Result: 0 errors, 0 warnings` summary and a non-empty output `.ex5`.

## Prerequisites

- A clean checkout on the configured branch (default `main`).
- A pre-existing local `.env`; the script never creates or prints secrets.
- Docker Compose, curl, Wine, winepath, timeout, and MetaEditor.
- By default, Wine prefix `~/.mt5` and terminal ID `D0E8209F77C8CF37AD8BF550E51FF075`.
- The real `RamonResearchTelemetry.mqh` dependency used by the strategy EA. The script looks in `mt5/`, the active Experts directory, and the terminal tree; if it cannot find the source, it stops before replacing the EAs. It will not invent a placeholder.

Optional overrides:

```bash
RAMON_REPO_DIR="$HOME/Documents/Presentation/ramon-ai-trader-bridge" \
RAMON_GIT_BRANCH=main \
RAMON_MT5_PREFIX="$HOME/.mt5" \
RAMON_MT5_TERMINAL_ID="D0E8209F77C8CF37AD8BF550E51FF075" \
bash scripts/update_build_ramon.sh
```

Use `RAMON_METAEDITOR` if MetaEditor is installed in a nonstandard location, and `RAMON_HEALTH_URL` only if the health endpoint intentionally differs.

## Failure and safety behavior

- Stops at the first failed prerequisite, migration, health check, compiler result, or missing output binary.
- Never runs `docker compose down`, removes volumes, resets the database, changes `.env` execution flags, attaches EAs to charts, or places trades.
- Does not claim that successful compilation proves strategy correctness or demo readiness.
- Django remains the intended authoritative strategy/risk service; deployment does not enable server-side command creation or execution.
