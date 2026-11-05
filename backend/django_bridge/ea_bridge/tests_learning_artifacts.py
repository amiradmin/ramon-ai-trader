import hashlib
import os
import tempfile
from unittest.mock import patch

from django.core.files.storage import default_storage
from django.test import TestCase, override_settings

from ea_bridge.management.commands.run_learning_training_worker import _run_model
from ea_bridge.models import LearningModelRun, LearningTrainingJob


class LearningModelArtifactTests(TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.settings_override = override_settings(MEDIA_ROOT=self.temp_dir.name)
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        self.job = LearningTrainingJob.objects.create(
            symbol="XAUUSD_o",
            timeframe="M15",
            requested_models=["trade_performance"],
        )
        self.run = LearningModelRun.objects.create(
            job=self.job,
            model_key="trade_performance",
            model_name="Performance · Random Forest",
        )

    def test_training_persists_versioned_pkl_and_checksum_even_when_validation_rejects(self):
        artifact = b"test-joblib-payload"
        trained = {
            "metrics": {"observations": 48, "target": "r_multiple"},
            "validation": {
                "passed": False,
                "checks": {"beats_training_median_baseline_on_mae_and_rmse": False},
            },
            "version": "trade-test-version",
            "artifact_bytes": artifact,
        }
        with patch(
            "ea_bridge.management.commands.run_learning_training_worker.train_trade_outcome_pipeline",
            return_value=trained,
        ):
            _run_model(self.job, self.run)

        self.run.refresh_from_db()
        self.assertEqual(self.run.status, LearningModelRun.Status.REJECTED)
        self.assertEqual(
            self.run.artifact_path,
            "learning_models/XAUUSD_o/trade_performance/trade-test-version.pkl",
        )
        self.assertEqual(self.run.artifact_sha256, hashlib.sha256(artifact).hexdigest())
        self.assertEqual(self.run.artifact_size_bytes, len(artifact))
        self.assertEqual(self.run.artifact_format, "joblib")
        self.assertNotIn("_artifact_joblib_b64", self.run.metrics)
        self.assertTrue(default_storage.exists(self.run.artifact_path))
        with default_storage.open(self.run.artifact_path, "rb") as saved:
            self.assertEqual(saved.read(), artifact)

    def test_training_run_without_artifact_keeps_artifact_fields_empty(self):
        self.run.artifact_path = ""
        self.run.save(update_fields=["artifact_path"])
        self.assertEqual(self.run.artifact_path, "")
