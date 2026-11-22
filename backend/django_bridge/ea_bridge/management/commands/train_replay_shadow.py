"""Train a *separate*, research-only expected-R model from a saved replay job.

Historical simulated fills are never inserted into LearningTradeOutcome and
this command does not change strategy thresholds or executable orders.
"""
from __future__ import annotations

import hashlib
import io
import math
from datetime import date, datetime, time, timedelta, timezone

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone as django_timezone

from ea_bridge.models import LearningModelRun, LearningTrainingJob, MarketBar
from ea_bridge.replay_backtest import _clean_m15_rows
from ea_bridge.replay_jobs import ROOT, read
from ea_bridge.replay_ml import replay_training_rows
from ea_bridge.management.commands.run_learning_training_worker import _persist_model_artifact

MODEL_KEY = "replay_expected_r"


def _find_job(job_id, symbol):
    if job_id:
        try:
            job = read(job_id)
        except (OSError, ValueError) as exc:
            raise ValueError("Replay job not found") from exc
        if job.get("status") != "COMPLETED":
            raise ValueError("Replay job must be completed")
        if (job.get("result") or {}).get("symbol") != symbol:
            raise ValueError("Replay job symbol mismatch")
        return job
    jobs = []
    for path in ROOT.glob("*.json"):
        try:
            job = read(path.stem)
        except (ValueError, OSError):
            continue
        if (job.get("status") == "COMPLETED" and isinstance(job.get("result"), dict)
                and job["result"].get("symbol") == symbol):
            jobs.append(job)
    if not jobs:
        raise ValueError("No completed replay job found for symbol; first run the backtest in the simulator")
    return max(jobs, key=lambda job: job.get("updated_at") or 0)


def _historical_bars(report):
    start = datetime.combine(date.fromisoformat(report["start"]), time.min, tzinfo=timezone.utc)
    end = datetime.combine(date.fromisoformat(report["end"]) + timedelta(days=1), time.min, tzinfo=timezone.utc)
    scope = {"symbol": report["symbol"], "source": report["source"], "timeframe": "M15"}
    prior = list(MarketBar.objects.filter(**scope, timestamp__lt=start).order_by("-timestamp")[:256])
    prior.reverse()
    selected = list(MarketBar.objects.filter(**scope, timestamp__gte=start, timestamp__lt=end).order_by("timestamp"))
    raw = [{"time": int(r.timestamp.timestamp()), "open": float(r.open),
            "high": float(r.high), "low": float(r.low), "close": float(r.close)}
           for r in (prior + selected)]
    return _clean_m15_rows(raw)[0]


def train_report(report):
    """Return training results + artifact, or fail explicitly (no optimistic fallback)."""
    from sklearn.feature_extraction import DictVectorizer
    from sklearn.ensemble import RandomForestRegressor
    from sklearn.metrics import mean_absolute_error, mean_squared_error
    import joblib

    if report.get("mode") != "ISOLATED_STRATEGY_RESEARCH_ESTIMATE" or report.get("timeframe") != "M15":
        raise ValueError("Only original isolated M15 strategy replay reports are eligible")
    bars = _historical_bars(report)
    rows = replay_training_rows(report, bars)
    if len(rows) < 48:
        raise ValueError(f"Insufficient independent replay labels: {len(rows)}; need at least 48")
    # Group on timestamp: multiple setups at the same instant cannot straddle
    # training and holdout. A final 20% chronological holdout remains untouched.
    times = sorted({x[0] for x in rows})
    if len(times) < 20:
        raise ValueError("Insufficient distinct signal timestamps for chronological validation")
    boundary = times[max(1, int(len(times)*0.8))]
    train = [r for r in rows if r[0] < boundary]
    test = [r for r in rows if r[0] >= boundary]
    if len(train) < 40 or len(test) < 8:
        raise ValueError("Need at least 40 training and 8 chronological holdout trades")
    vectorizer = DictVectorizer(sparse=False, sort=True)
    x_train = vectorizer.fit_transform([r[1] for r in train])
    x_test = vectorizer.transform([r[1] for r in test])
    y_train = [r[2] for r in train]
    y_test = [r[2] for r in test]
    model = RandomForestRegressor(n_estimators=160, min_samples_leaf=3,
                                  max_features=0.8, random_state=42, n_jobs=1)
    model.fit(x_train, y_train)
    predictions = model.predict(x_test)
    median = sorted(y_train)[len(y_train)//2]
    baseline = [median]*len(y_test)
    mae = float(mean_absolute_error(y_test, predictions))
    rmse = float(math.sqrt(mean_squared_error(y_test, predictions)))
    baseline_mae = float(mean_absolute_error(y_test, baseline))
    baseline_rmse = float(math.sqrt(mean_squared_error(y_test, baseline)))
    passed = (all(map(math.isfinite, (mae, rmse, baseline_mae, baseline_rmse)))
              and mae < baseline_mae and rmse < baseline_rmse)
    if passed:
        vectorizer = DictVectorizer(sparse=False, sort=True)
        x_all = vectorizer.fit_transform([r[1] for r in rows])
        model = RandomForestRegressor(n_estimators=160, min_samples_leaf=3,
                                      max_features=0.8, random_state=42, n_jobs=1)
        model.fit(x_all, [r[2] for r in rows])
    buff = io.BytesIO()
    joblib.dump({"model_key": MODEL_KEY, "target": "r_multiple",
                 "vectorizer": vectorizer, "estimator": model,
                 "feature_names": list(vectorizer.feature_names_)}, buff)
    data = buff.getvalue()
    return {
        "metrics": {"observations": len(rows), "training_observations": len(train),
                    "test_observations": len(test), "distinct_signal_times": len(times),
                    "mae": mae, "rmse": rmse, "baseline_mae": baseline_mae,
                    "baseline_rmse": baseline_rmse,
                    "source": "SIMULATED_REPLAY_NOT_BROKER_TRADES",
                    "history_fingerprint": report["data_quality"]["history_fingerprint"],
                    "test_start_utc": datetime.fromtimestamp(boundary, tz=timezone.utc).isoformat()},
        "validation": {"passed": passed,
                       "note": "Research-only simulated trade outcomes; not authorized for execution or risk changes.",
                       "checks": {"chronological_grouped_holdout": True,
                                  "beats_median_baseline_mae_rmse": passed}},
        "version": "replay-" + hashlib.sha256(data).hexdigest()[:16],
        "artifact_bytes": data,
    }


class Command(BaseCommand):
    help = "Train an isolated shadow-only expected-R model from the existing saved M15 replay result."

    def add_arguments(self, parser):
        parser.add_argument("--symbol", default="XAUUSD_l")
        parser.add_argument("--job-id", default="")

    def handle(self, *args, **options):
        symbol = options["symbol"]
        try:
            job_data = _find_job(options["job_id"], symbol)
            report = job_data["result"]
            self.stdout.write(f"Replay source={report['source']} symbol={symbol}, period={report['start']}..{report['end']}")
            trained = train_report(report)
        except (KeyError, TypeError, ValueError) as exc:
            raise CommandError(str(exc)) from exc
        # Explicit research run, not a regular trade-outcome training job.
        with transaction.atomic():
            job = LearningTrainingJob.objects.create(
                symbol=symbol, timeframe="M15", source=report["source"],
                requested_models=[MODEL_KEY], created_by="replay-shadow-command",
                status=LearningTrainingJob.Status.COMPLETED,
                started_at=django_timezone.now(), finished_at=django_timezone.now())
            run = LearningModelRun(
                job=job, model_key=MODEL_KEY, model_name="Replay expected R · Random Forest",
                status=(LearningModelRun.Status.PASSED if trained["validation"]["passed"]
                        else LearningModelRun.Status.REJECTED),
                metrics=trained["metrics"], validation=trained["validation"],
                version=trained["version"], training_started_at=django_timezone.now(),
                training_finished_at=django_timezone.now(),
            )
            _persist_model_artifact(job, run, trained["artifact_bytes"])
            run.save()
        self.stdout.write(self.style.SUCCESS(
            f"Replay ML research run={run.pk} status={run.status} "
            f"labels={trained['metrics']['observations']} "
            f"MAE={trained['metrics']['mae']:.4f} baseline={trained['metrics']['baseline_mae']:.4f}; "
            "NO LIVE TRADING INFLUENCE"))
