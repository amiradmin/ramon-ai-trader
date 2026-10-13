# Ramon boot startup (Ubuntu + Docker Compose)

All currently active long-running Compose services already specify
`restart: unless-stopped`, including `model`, dashboards,
`postgres-logs` and `research-log-ingest`.
The `tools` one-shot utility intentionally does not restart.
Docker restart policies alone do **not** start a profile service which
was never created or was manually stopped.

The optional host-level systemd unit restores the base and `logging`
Compose profile after a reboot. It uses `--no-build --no-recreate` so it
does not rebuild or recreate active containers, and does not modify
SQLite/PostgreSQL or trading settings. Model health dependencies remain
under Docker Compose control. The unit retries failed startup.

## Install (once on HP-Mini)

```bash
cd ~/Documents/Presentation/ramon-ai-trader
bash scripts/install_boot_service.sh
sudo systemctl start ramon-compose.service
systemctl status ramon-compose.service --no-pager
docker compose --profile logging ps
```

The script first validates Compose and Docker access; install only
after confirming the configured `logging` services are desired.
`RAMON_LOG_DB_PASSWORD` must already be configured securely in the
existing Compose configuration. Do not paste or commit passwords.

For reboot persistence of the Docker daemon, check
`systemctl is-enabled docker.service`; if not enabled, use
`sudo systemctl enable docker.service`.

No extra Compose profiles (`advisors`, `mt5`,
`postgres-migration`, `tools`) are launched by this unit.
Host-installed MetaTrader running outside Docker is **not** managed by
this unit: EA auto-run requires its own desktop/session/service setup.

## Verification after reboot

```bash
systemctl status ramon-compose.service --no-pager
docker compose --profile logging ps
curl -fsS http://127.0.0.1:8012/health
curl -fsS http://127.0.0.1:8013/monitor -o /dev/null
curl -fsS http://127.0.0.1:8015/ -o /dev/null
```

Do not use `docker compose down -v` or indiscriminately prune volumes;
PostgreSQL persistence lives in named volumes.
To disable boot orchestration: `sudo systemctl disable --now ramon-compose.service`.
That disables the unit; existing container restart policies still apply.
