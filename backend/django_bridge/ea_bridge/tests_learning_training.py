import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.core.files.storage import default_storage

from ea_bridge.models import LearningModelRun, LearningTrainingJob, MarketBar


class LearningTrainingApiTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="learning-staff", password="test-password", is_staff=True
        )
        self.client.force_login(self.user)
        start = datetime(2026, 10, 1, tzinfo=timezone.utc)
        for index in range(60):
            close = 2300 + index * 0.05
            MarketBar.objects.create(
                source="training-test-ea", symbol="XAUUSD_o", timeframe="M15",
                timestamp=start + timedelta(minutes=15 * index),
                open=close - 0.02, high=close + 0.1, low=close - 0.1,
                close=close, volume=1,
            )

    def test_start_training_queues_all_registered_models(self):
        response = self.client.post(
            "/api/ea/v1/learning/training/start",
            data=json.dumps({"symbol": "XAUUSD_o", "timeframe": "M15"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 202, response.content)
        job = LearningTrainingJob.objects.get()
        self.assertEqual(job.status, LearningTrainingJob.Status.QUEUED)
        self.assertEqual(set(job.requested_models), {"arima", "sarima", "ridge", "random_forest", "trade_performance", "trade_decision", "trade_entry_exit", "trade_regime", "trade_mistake", "trade_strategy"})
        self.assertEqual(set(job.model_runs.values_list("model_key", flat=True)), set(job.requested_models))


    def test_trade_only_training_does_not_require_market_candles(self):
        MarketBar.objects.all().delete()
        response = self.client.post(
            "/api/ea/v1/learning/training/start",
            data=json.dumps({"models": ["trade_performance", "trade_decision"]}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 202, response.content)
        job = LearningTrainingJob.objects.get()
        self.assertEqual(set(job.requested_models), {"trade_performance", "trade_decision"})
        self.assertEqual(job.source, "")

    def test_worker_runs_trade_outcome_pipeline_and_persists_artifact(self):
        from ea_bridge.management.commands.run_learning_training_worker import process_one_job

        job = LearningTrainingJob.objects.create(
            requested_models=["trade_performance"],
        )
        run = LearningModelRun.objects.create(
            job=job, model_key="trade_performance", model_name="Performance · Random Forest"
        )
        artifact_bytes = b"fake-joblib-model-artifact"
        trained = {
            "metrics": {"observations": 60, "mae": 0.4, "rmse": 0.6, "baseline_mae": 0.8, "baseline_rmse": 1.0},
            "validation": {"passed": True, "checks": {"no_post_trade_features": True}},
            "artifact_bytes": artifact_bytes,
            "version": "trade-test123",
        }
        with patch(
            "ea_bridge.management.commands.run_learning_training_worker.train_trade_outcome_pipeline",
            return_value=trained,
        ):
            self.assertTrue(process_one_job())
        run.refresh_from_db()
        self.assertEqual(run.status, LearningModelRun.Status.PASSED)
        self.assertEqual(run.artifact_format, "joblib")
        self.assertEqual(run.artifact_size_bytes, len(artifact_bytes))
        self.assertTrue(run.artifact_path)
        self.assertTrue(run.artifact_sha256)
        with default_storage.open(run.artifact_path, "rb") as artifact_file:
            self.assertEqual(artifact_file.read(), artifact_bytes)
        self.assertTrue(run.validation["checks"]["no_post_trade_features"])

    def test_rejects_ambiguous_market_data_sources(self):
        MarketBar.objects.create(
            source="second-ea", symbol="XAUUSD_o", timeframe="M15",
            timestamp=datetime(2026, 10, 2, tzinfo=timezone.utc),
            open=2300, high=2301, low=2299, close=2300, volume=1,
        )
        response = self.client.post(
            "/api/ea/v1/learning/training/start",
            data=json.dumps({"symbol": "XAUUSD_o", "timeframe": "M15"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("Multiple MarketBar sources", response.json()["error"])

    def test_prevents_a_second_active_training_job(self):
        LearningTrainingJob.objects.create(
            symbol="XAUUSD_o", timeframe="M15",
            status=LearningTrainingJob.Status.QUEUED,
        )
        response = self.client.post(
            "/api/ea/v1/learning/training/start",
            data=json.dumps({"symbol": "XAUUSD_o", "timeframe": "M15"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 409)

    def test_training_status_requires_staff_session(self):
        self.client.logout()
        response = self.client.get("/api/ea/v1/learning/training/status")
        self.assertEqual(response.status_code, 403)

    def test_worker_records_validation_and_finishes_job(self):
        from ea_bridge.management.commands.run_learning_training_worker import process_one_job

        job = LearningTrainingJob.objects.create(
            symbol="XAUUSD_o", timeframe="M15", source="training-test-ea",
            requested_models=["arima"],
        )
        run = LearningModelRun.objects.create(
            job=job, model_key="arima", model_name="ARIMA"
        )
        fake = {
            "mae": 1.0, "rmse": 1.2, "baseline_mae": 2.0,
            "baseline_rmse": 2.2, "directional_accuracy": 0.6,
            "convergence_warnings": 0, "test_observations": 20,
            "observations": 60, "evaluation_method": "rolling_one_step",
        }
        with patch(
            "ea_bridge.management.commands.run_learning_training_worker.forecast_market_bars"
        ) as forecast:
            forecast.return_value.as_dict.return_value = fake
            self.assertTrue(process_one_job())
        run.refresh_from_db()
        job.refresh_from_db()
        self.assertEqual(run.status, LearningModelRun.Status.PASSED)
        self.assertTrue(run.validation["passed"])
        self.assertTrue(run.version.startswith("eval-"))
        self.assertEqual(job.status, LearningTrainingJob.Status.COMPLETED)
