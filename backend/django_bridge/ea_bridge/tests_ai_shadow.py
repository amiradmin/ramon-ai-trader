"""Contract tests: AI shadow must not invent probabilities or affect execution."""
import hashlib
import io
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from ea_bridge.ai_shadow import observe_shadow


class ShadowScoringTests(SimpleTestCase):
    def test_wait_never_loads_model(self):
        with patch("ea_bridge.ai_shadow.LearningModelRun.objects") as models:
            result = observe_shadow({"decision": "WAIT"}, symbol="XAUUSD_l", setup="TREND_PULLBACK")
        self.assertEqual(result["status"], "NO_CANDIDATE")
        models.assert_not_called()

    def test_no_validated_model_has_no_prediction(self):
        with patch("ea_bridge.ai_shadow.LearningModelRun.objects") as models:
            models.select_related.return_value.filter.return_value.exclude.return_value.order_by.return_value.first.return_value = None
            result = observe_shadow({"decision": "BUY"}, symbol="XAUUSD_l", setup="TREND_PULLBACK")
        self.assertEqual(result["status"], "NO_VALIDATED_MODEL")
        self.assertNotIn("expected_r", result)

    def test_checksum_mismatch_rejects_artifact_without_unpickling(self):
        run = SimpleNamespace(artifact_format="joblib", artifact_path="learning_models/a.pkl",
                              artifact_sha256="0" * 64, artifact_size_bytes=4)
        with patch("ea_bridge.ai_shadow.LearningModelRun.objects") as models, \\
             patch("ea_bridge.ai_shadow.default_storage.open", return_value=io.BytesIO(b"data")), \\
             patch("joblib.load") as load:
            models.select_related.return_value.filter.return_value.exclude.return_value.order_by.return_value.first.return_value = run
            result = observe_shadow({"decision": "SELL"}, symbol="XAUUSD_l", setup="RANGE_EDGE_FADE")
        self.assertEqual(result["status"], "ARTIFACT_MISMATCH")
        load.assert_not_called()

    def test_valid_model_yields_expected_r_not_win_probability(self):
        raw = b"validated-internal-training-artifact"
        run = SimpleNamespace(artifact_format="joblib", artifact_path="learning_models/model.pkl",
                              artifact_sha256=hashlib.sha256(raw).hexdigest(),
                              artifact_size_bytes=len(raw), version="trade-v1")
        vectorizer = MagicMock()
        estimator = MagicMock()
        estimator.predict.return_value = [0.31]
        bundle = {"model_key": "trade_performance", "target": "r_multiple",
                  "feature_names": ["tag_side", "tag_strategy_profile", "tag_score"],
                  "vectorizer": vectorizer, "estimator": estimator}
        candidate = {"decision": "BUY", "stages": {
            "opportunities": {"score": 0.8, "setup": {"type": "TREND_PULLBACK"}},
            "entry_timing": {"spread_points": 20}}}
        with patch("ea_bridge.ai_shadow.LearningModelRun.objects") as models, \\
             patch("ea_bridge.ai_shadow.default_storage.open", return_value=io.BytesIO(raw)), \\
             patch("joblib.load", return_value=bundle):
            models.select_related.return_value.filter.return_value.exclude.return_value.order_by.return_value.first.return_value = run
            result = observe_shadow(candidate, symbol="XAUUSD_l", setup="TREND_PULLBACK")
        self.assertEqual(result["status"], "SCORED")
        self.assertEqual(result["expected_r"], 0.31)
        self.assertNotIn("win_probability", result)
        self.assertEqual(result["mode"], "SHADOW_ONLY")
