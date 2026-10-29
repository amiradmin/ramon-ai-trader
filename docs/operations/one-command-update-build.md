# One-command Ramon update and build

From the existing local clone, run:

```bash
cd ~/Documents/Presentation/ramon-ai-trader-bridge
bash scripts/update_build_ramon.sh
```

The script fast-forward pulls `origin/main`, validates the repository's single
`docker-compose.yaml`, rebuilds and starts the Compose services, applies forward
Django migrations, runs Django's system check, waits for `/healthz`, and compiles
the repository's current `mt5/RamonCommandEA_v1.02.mq5` into the configured
active MT5 terminal's `MQL5/Experts` directory using MetaEditor under Wine.

## Prerequisites

- Run it from a clean `main` checkout. It refuses to overwrite uncommitted work.
- A local `.env` file must already exist. The script never creates or prints secrets.
- Docker Compose, curl, Wine, and the MetaEditor executable must be installed.
- By default, the active terminal ID is
  `D0E8209F77C8CF37AD8BF550E51FF075` and the Wine prefix is `~/.mt5`.

Optional environment overrides:

```bash
RAMON_REPO_DIR="$HOME/Documents/Presentation/ramon-ai-trader-bridge" \
RAMON_GIT_BRANCH=main \
RAMON_MT5_PREFIX="$HOME/.mt5" \
RAMON_MT5_TERMINAL_ID="D0E8209F77C8CF37AD8BF550E51FF075" \
bash scripts/update_build_ramon.sh
```

If MetaEditor is installed in a nonstandard location, set `RAMON_METAEDITOR`
to its exact executable path. Set `RAMON_HEALTH_URL` only if the local health
endpoint is intentionally different.

## Safety and scope

- The script does **not** run `docker compose down`, remove volumes, reset the
  database, enable EA command execution, change `.env` trading flags, attach an
  EA to a chart, or place trades.
- It backs up any existing v1.02 source and binary before replacing/compiling
  them. Compilation must report zero errors and zero warnings.
- If a step fails, it stops and prints the relevant health/log/compile evidence.
- It compiles and installs only the repository's current command EA.
  `Ramon_v0.62.x.mq5` strategy sources are not present in this repository, so the
  script will not invent, overwrite, or compile those local files. They must be
  added to GitHub and reviewed before an automated build can include them.
- The strategy EA versions previously found in the local terminal contain
  `EnableLiveTrading=true` and a Django decision API call. Do not run them beside
  the command EA until the exact source has been reviewed and the execution
  architecture is reconciled. Django must remain the only strategy/risk brain.
- A successful build is not authorization to enable trading. Keep
  `EnableCommandExecution=false` and both server command flags disabled until
  the demo-only activation checklist in
  [demo-trading-activation.md](demo-trading-activation.md) is satisfied.
