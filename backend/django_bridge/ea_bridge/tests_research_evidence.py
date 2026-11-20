from django.test import SimpleTestCase
from .research_evidence import assess_evidence


class ResearchEvidenceTests(SimpleTestCase):
    def test_crossing_zero_is_inconclusive(self):
        result = assess_evidence(trades=100, interval=[-0.1, 0.2])
        self.assertEqual(result['interval_assessment'], 'INCONCLUSIVE')
        self.assertEqual(result['status'], 'VALIDATION_PENDING')
        self.assertFalse(result['execution_authority'])

    def test_positive_interval_cannot_promote_even_with_attestations(self):
        result = assess_evidence(trades=100, interval=[0.1, 0.2], dataset_verified=True,
                                 holdout_untouched=True, selection_adjusted=True,
                                 cost_sensitivity_verified=True, forward_demo_verified=True)
        self.assertEqual(result['interval_assessment'], 'POSITIVE_INTERVAL')
        self.assertEqual(result['status'], 'VALIDATION_PENDING')
        self.assertFalse(result['execution_authority'])

    def test_negative_interval_is_not_automatic_suspension(self):
        self.assertEqual(assess_evidence(trades=100, interval=[-0.4, -0.1])['interval_assessment'], 'NEGATIVE_INTERVAL')

    def test_invalid_inputs_are_rejected(self):
        for trades, interval in [(0, [0.1, 0.2]), (2, [1, -1]), (2, [float('nan'), 1])]:
            with self.subTest(trades=trades, interval=interval), self.assertRaises(ValueError):
                assess_evidence(trades=trades, interval=interval)
