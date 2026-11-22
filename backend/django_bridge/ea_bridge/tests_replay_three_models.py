"""Three replay research models: forward-label boundaries and shadow isolation."""
from unittest.mock import patch

from django.test import SimpleTestCase

from ea_bridge.replay_ml_models import regime_rows, _chronological_partition
from ea_bridge.tests_replay_ml import synthetic_bars


class ThreeReplayModelsTests(SimpleTestCase):
    def test_market_regime_label_uses_future_prices_but_features_do_not(self):
        bars = synthetic_bars(120)
        rows = regime_rows(bars)
        self.assertTrue(rows)
        ts, features, label = rows[20]
        self.assertIn(label, {"UP", "DOWN", "RANGE"})
        self.assertNotIn("tag_side", features)
        self.assertNotIn("tag_setup_type", features)
        altered = [dict(b) for b in bars]
        for bar in altered:
            if bar["time"] > ts:
                bar["close"] += 400
        changed = next(item for item in regime_rows(altered) if item[0] == ts)
        self.assertEqual(features, changed[1])

    def test_forward_label_train_holdout_purge(self):
        rows = [(i*900, {"snapshot_atr": 1.0}, "RANGE") for i in range(100)]
        train, holdout, boundary = _chronological_partition(rows, horizon_seconds=3600)
        self.assertTrue(all(row[0] + 3600 < boundary for row in train))
        self.assertTrue(all(row[0] >= boundary for row in holdout))

    def test_classifiers_are_never_requested_for_live_execution(self):
        from ea_bridge.ai_shadow import observe_replay_classifiers
        bars = synthetic_bars()
        with patch("ea_bridge.ai_shadow.LearningModelRun.objects") as models:
            values = observe_replay_classifiers(
                {"decision": "WAIT"}, symbol="XAUUSD_l", setup="TREND_PULLBACK",
                market_bars=bars, spread_points=15)
        self.assertEqual(values["replay_trade_success"]["status"], "NO_CANDIDATE")
        self.assertEqual(values["replay_market_regime"]["status"], "NO_VALIDATED_MODEL")
        self.assertEqual(values["replay_market_regime"]["mode"], "SHADOW_ONLY")
