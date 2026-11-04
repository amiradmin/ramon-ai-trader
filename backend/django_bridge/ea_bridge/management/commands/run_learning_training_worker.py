"""Background worker for user-triggered, research-only learning model retraining."""
from __future__ import annotations

import hashlib
import json
import time

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from ea_bridge.models import LearningModelRun, LearningTrainingJob, MarketBar
from ramon_pipeline.research_forecasting import evaluate_regression_forecasts
from ramon_pipeline.learning.regression_pipelines import PIPELINES, train_trade_outcome_pipeline
from ramon_pipeline.signalflow.forecasting import (
    forecast_market_bars,
    forecast_market_bars_sarima,
)


MODEL_REGISTRY = {
    "arima": ("ARIMA", forecast_market_bars),
    "sarima": ("SARIMA", forecast_market_bars_sarima),
    "ridge": ("Ridge Regression", None),
    "random_forest": ("Random Forest", None),
}


def _claim_job():
    with transaction.atomic():
        job = (
            LearningTrainingJob.objects.select_for_update(skip_locked=True)
            .filter(status=LearningTrainingJob.Status.QUEUED)
            .order_by("created_at")
            .first()
        )
        if job is None:
            return None
        job.status = LearningTrainingJob.Status.RUNNING
        job.started_at = timezone.now()
        job.save(update_fields=["status", "started_at"])
        job.model_runs.filter(status=LearningModelRun.Status.QUEUED).update(
            status=LearningModelRun.Status.RUNNING,
            training_started_at=timezone.now(),
        )
        return job.pk


def _run_model(job, run):
    run.training_started_at = timezone.now()
    run.status = LearningModelRun.Status.RUNNING
    run.save(update_fields=["training_started_at", "status"])
    try:
        if run.model_key in PIPELINES:
            trained = train_trade_outcome_pipeline(run.model_key)
            run.metrics = {**trained["metrics"], "_artifact_joblib_b64": trained["artifact"]}
            run.validation = trained["validation"]
            run.version = trained["version"]
            run.status = (
                LearningModelRun.Status.PASSED
                if trained["validation"]["passed"]
                else LearningModelRun.Status.REJECTED
            )
            return
        query = MarketBar.objects.filter(
            symbol=job.symbol, timeframe=job.timeframe
        )
        if job.source:
            query = query.filter(source=job.source)
        sources = list(query.values_list("source", flat=True).distinct()[:2])
        if not sources:
            raise ValueError("No MarketBar candles exist for this symbol/timeframe.")
        if not job.source and len(sources) > 1:
            raise ValueError(
                "Multiple MarketBar sources exist. Start training with an explicit source to avoid mixing feeds."
            )
        if run.model_key == "arima":
            result = forecast_market_bars(
                symbol=job.symbol, timeframe=job.timeframe,
                source=job.source or sources[0], limit=500, test_size=20,
            ).as_dict()
        elif run.model_key == "sarima":
            result = forecast_market_bars_sarima(
                symbol=job.symbol, timeframe=job.timeframe,
                source=job.source or sources[0], limit=500, test_size=20,
            ).as_dict()
        elif run.model_key in {"ridge", "random_forest"}:
            bars = list(query.order_by("-timestamp").only(
                "timestamp", "open", "high", "low", "close", "volume"
            )[:500])
            bars.reverse()
            candles = [{
                "timestamp": bar.timestamp.isoformat(),
                "open": float(bar.open), "high": float(bar.high),
                "low": float(bar.low), "close": float(bar.close),
                "tick_volume": float(bar.volume),
            } for bar in bars]
            reports = evaluate_regression_forecasts(candles, horizon_bars=4)
            result = reports[run.model_key].as_dict()
        else:
            raise ValueError("Model is not registered for training.")

        finite = all(
            isinstance(result.get(key), (int, float))
            and not isinstance(result.get(key), bool)
            for key in ("mae", "rmse", "baseline_mae", "baseline_rmse", "directional_accuracy")
        )
        beats_baseline = (
            finite
            and result["mae"] < result["baseline_mae"]
            and result["rmse"] < result["baseline_rmse"]
        )
        no_convergence_warnings = result.get("convergence_warnings", 0) == 0
        enough_holdout = result.get("test_observations", 0) >= 5
        regression_baseline = run.model_key in {"ridge", "random_forest"}
        beats_baseline = (
            bool(result.get("beats_zero_return_baseline"))
            if regression_baseline else beats_baseline
        )
        passed = bool(
            finite and beats_baseline and enough_holdout
            and (regression_baseline or no_convergence_warnings)
        )
        validation = {
            "passed": passed,
            "checks": {
                "finite_metrics": finite,
                ("beats_zero_return_baseline_on_mae_and_rmse" if regression_baseline
                 else "beats_persistence_baseline_on_mae_and_rmse"): beats_baseline,
                "minimum_five_holdout_observations": enough_holdout,
                "no_convergence_warnings": no_convergence_warnings if not regression_baseline else None,
            },
            "evaluation_method": result.get("evaluation_method"),
            "note": "Research validation only; this report does not deploy a model or authorize trading.",
        }
        fingerprint = hashlib.sha256(
            json.dumps(result, sort_keys=True, separators=(",", ":"), default=str).encode()
        ).hexdigest()[:16]
        run.metrics = result
        run.validation = validation
        run.version = f"eval-{fingerprint}"
        run.status = (
            LearningModelRun.Status.PASSED if passed
            else LearningModelRun.Status.REJECTED
        )
    except Exception as exc:
        run.status = LearningModelRun.Status.FAILED
        run.error = str(exc)[:4000]
    finally:
        run.training_finished_at = timezone.now()
        run.save(update_fields=[
            "status", "metrics", "validation", "version", "error",
            "training_started_at", "training_finished_at",
        ])


def process_one_job():
    job_id = _claim_job()
    if job_id is None:
        return False
    job = LearningTrainingJob.objects.get(pk=job_id)
    for run in job.model_runs.order_by("id"):
        _run_model(job, run)
    statuses = list(job.model_runs.values_list("status", flat=True))
    passed = statuses.count(LearningModelRun.Status.PASSED)
    failed = statuses.count(LearningModelRun.Status.FAILED)
    rejected = statuses.count(LearningModelRun.Status.REJECTED)
    if passed == len(statuses):
        job.status = LearningTrainingJob.Status.COMPLETED
    elif passed or rejected:
        job.status = LearningTrainingJob.Status.PARTIAL
    else:
        job.status = LearningTrainingJob.Status.FAILED
    job.finished_at = timezone.now()
    if failed or rejected:
        job.error = f"{failed} model execution failure(s), {rejected} validation rejection(s)."
    job.save(update_fields=["status", "finished_at", "error"])
    return True


class Command(BaseCommand):
    help = "Poll the database queue and execute learning model training/validation jobs."

    def add_arguments(self, parser):
        parser.add_argument("--poll-seconds", type=float, default=2.0)
        parser.add_argument("--once", action="store_true")

    def handle(self, *args, **options):
        poll = max(0.5, options["poll_seconds"])
        self.stdout.write("Learning training worker started.")
        while True:
            processed = process_one_job()
            if options["once"]:
                return
            if not processed:
                time.sleep(poll)
