"""Research replay ML safety and leakage checks."""
from datetime import datetime, timezone
from unittest.mock import patch

from django.test import SimpleTestCase

from ea_bridge.replay_ml import make_features, replay_training_rows


def synthetic_bars(count=90, start=1_792_000_800):
    # Exact UTC M15 slots only.
    start -= start % 900
    return [{"time": start + i*900,
             "open": 3500+i*0.2,
             "high": 3501+i*0.2,
             "low": 3499+i*0.2,
             "close": 3500.2+i*0.2} for i in range(count)]


class ReplayMLTests(SimpleTestCase):
    def test_features_stop_at_signal_bar(self):
        bars = synthetic_bars()
        signal = bars[75]["time"]
        base = make_features(bars, signal_at=signal, setup="TREND_PULLBACK",
                             side="BUY", spread_points=20)
        altered = [dict(b) for b in bars]
        for b in altered[76:]:
            b["close"] = 999999
        other = make_features(altered, signal_at=signal, setup="TREND_PULLBACK",
                              side="BUY", spread_points=20)
        self.assertEqual(base, other)

    def test_missing_signal_bar_rejected(self):
        bars = synthetic_bars()
        with self.assertRaisesRegex(ValueError, "exact completed"):
            make_features(bars, signal_at=bars[75]["time"] + 900*20,
                          setup="TREND_PULLBACK", side="SELL", spread_points=20)

    def test_replay_quality_guard_prevents_bad_data(self):
        report = {"assumptions": {"spread_points": 20},
                  "data_quality": {"history_fingerprint": "abc", "excluded_fraction": 0.21},
                  "strategies": []}
        with self.assertRaisesRegex(ValueError, "More than 5%"):
            replay_training_rows(report, synthetic_bars())

    def test_shadow_isolated_from_market_execution(self):
        from ea_bridge.ai_shadow import observe_replay_shadow
        with patch("ea_bridge.ai_shadow.LearningModelRun.objects") as manager:
            result = observe_replay_shadow({"decision": "WAIT"}, symbol="XAUUSD_l",
                setup="TREND_PULLBACK", market_bars=synthetic_bars(), spread_points=20)
        self.assertEqual(result["status"], "NO_CANDIDATE")
        self.assertEqual(result["training_source"], "SIMULATED_REPLAY")
        manager.assert_not_called()
