# Ramon learning model artifacts

## Storage contract

The six trade-outcome learning pipelines write one immutable Joblib-compatible
`.pkl` artifact for every successful fit. Artifacts are stored in Django's
configured `MEDIA_ROOT`, not in Git and not inside PostgreSQL JSON fields.

Default container path:

```text
/app/media/learning_models/<symbol>/<model_key>/<version>.pkl
```

For example:

```text
/app/media/learning_models/XAUUSD_o/trade_performance/trade-<sha256-prefix>.pkl
/app/media/learning_models/XAUUSD_o/trade_decision/trade-<sha256-prefix>.pkl
/app/media/learning_models/XAUUSD_o/trade_entry_exit/trade-<sha256-prefix>.pkl
/app/media/learning_models/XAUUSD_o/trade_regime/trade-<sha256-prefix>.pkl
/app/media/learning_models/XAUUSD_o/trade_mistake/trade-<sha256-prefix>.pkl
/app/media/learning_models/XAUUSD_o/trade_strategy/trade-<sha256-prefix>.pkl
```

The `learning_models` Docker named volume is mounted at `/app/media` in both
the Django and learning-worker services. It survives normal container rebuilds
and `docker compose down`; do not use `docker compose down -v` unless you
intentionally want to delete model artifacts.

Each `LearningModelRun` stores the relative artifact path, SHA-256 checksum,
byte size, format, model version, metrics and validation report. The artifact
is saved even when validation rejects the candidate, so rejected candidates
remain available for inspection and comparison. Only a validation-passed run
is eligible for a later promotion review.

## Operational checks

List artifacts inside the Django container:

```bash
docker compose exec django find /app/media/learning_models -type f -name '*.pkl' -printf '%p %s bytes\\n'
```

Inspect artifact metadata from Django:

```bash
docker compose exec django python manage.py shell -c "from ea_bridge.models import LearningModelRun; print(list(LearningModelRun.objects.exclude(artifact_path='').values('model_key','status','version','artifact_path','artifact_sha256','artifact_size_bytes')))"
```

Back up the named volume before destructive Docker maintenance. A simple
archive can be created with:

```bash
docker run --rm -v ramon_learning_models:/models -v "$PWD:/backup" alpine \\
  tar -czf /backup/ramon-learning-models.tar.gz -C /models .
```

Check the actual volume name with `docker volume ls`; Compose may prefix it
with the project name.

## Future production promotion requirements

Saving a file does not deploy it. No live decision code loads these artifacts
as part of this change. Before a candidate can influence Ramon decisions, a
separate promotion workflow must:

1. Require a validation-passed run and verify the artifact SHA-256 before loading.
2. Verify model key, symbol, version, training-data window, dependency versions,
   and the exact feature schema used at training.
3. Use the same versioned feature-building code at inference; missing or
   incompatible inputs must fail closed to the existing deterministic
   SignalFlow path.
4. Evaluate the candidate with walk-forward/out-of-sample tests, realistic
   spread, commission, slippage, and execution assumptions, plus a demo
   shadow period and explicit human approval.
5. Keep the existing Django risk manager and all account, news, exposure,
   stop-loss, and execution gates authoritative. A model must never set lot
   size or bypass risk controls.
6. Support rollback to the previous approved model version and record which
   model version influenced every decision.

The MT5 EA remains a telemetry/execution bridge only. Model inference and
promotion decisions belong in Django.
