# Automated training replication (opt-in)

This separate worker synchronizes SQLite decision samples and trade outcomes to
PostgreSQL every 15 minutes. SQLite remains authoritative; there is no live-EA
or live-model restart. It rechecks existing rows so updates to trade results
are also replicated.

To activate after merge:
```bash
git switch main && git pull --ff-only origin main
bash scripts/activate_pg_training_sync.sh
```

The script verifies prerequisites, tests, Compose configuration, creates only
the independent worker and checks PostgreSQL row totals. It never runs
`docker compose down`, never deletes volumes, and never touches the EA.

To stop just the background worker:
```bash
docker compose -f compose.yaml -f compose.pg-training.yaml \
  --profile pg-training stop pg-training-sync
```

Training readers are still opt-in via `RAMON_TRAINING_BACKEND=postgres`;
this worker is eventually consistent, not a transactional dual-write. During
the first sync it may consume disk I/O as it scans ~100k rows. Do not remove
SQLite until every remaining live reader/writer has been migrated and proven.
