from dataclasses import asdict
from django.test import SimpleTestCase
from ramon_pipeline.signalflow.scalping import ScalpConfig
from .scalp_policy import validated_policy


class ScalpPolicyTests(SimpleTestCase):
    def test_defaults_and_custom_values(self):
        self.assertEqual(validated_policy({}), ScalpConfig())
        policy = validated_policy({'m1_window': '80', 'max_holding_bars': '3', 'reward_risk': '1.50'})
        self.assertEqual(policy.m1_window, 80)
        self.assertEqual(policy.max_holding_bars, 3)
        self.assertEqual(validated_policy(asdict(policy)), policy)

    def test_rejects_invalid_or_weakened_protections(self):
        for overrides in ({'maximum_cost_to_risk': .25}, {'max_holding_bars': 6},
                          {'m1_window': 64.5}, {'reward_risk': 'NaN'},
                          {'minimum_stop_atr': 2, 'maximum_stop_atr': 1}, {'unknown': 1}):
            with self.assertRaises(ValueError):
                validated_policy(overrides)
