from django.test import SimpleTestCase

from .management.commands.evaluate_trend_pullback import BASE, RETRACEMENT_VARIANTS


class RetracementSensitivityTests(SimpleTestCase):
    def test_only_maximum_retracement_changes(self):
        self.assertEqual(
            [v["max_retracement"] for v in RETRACEMENT_VARIANTS.values()],
            [0.82, 0.85, 0.88, 0.90, 0.95],
        )
        self.assertEqual(RETRACEMENT_VARIANTS["baseline"], BASE)
        for params in RETRACEMENT_VARIANTS.values():
            self.assertEqual(
                {k: v for k, v in params.items() if k != "max_retracement"},
                {k: v for k, v in BASE.items() if k != "max_retracement"},
            )
