from django.test import SimpleTestCase
from .replay_backtest import _derive_m5_rows


class M5DerivationTests(SimpleTestCase):
    def setUp(self):
        self.raw = [dict(time=300+i*60, open=100+i, high=102+i,
                         low=99+i, close=101+i) for i in range(5)]

    def test_complete_group(self):
        rows, quality = _derive_m5_rows(self.raw)
        self.assertEqual(rows, [dict(time=300, open=100, high=106, low=99, close=105)])
        self.assertEqual(quality["incomplete_or_invalid_groups"], 0)

    def test_missing_minute(self):
        self.assertEqual(_derive_m5_rows(self.raw[:-1])[0], [])

    def test_duplicate_minute(self):
        self.assertEqual(_derive_m5_rows(self.raw+[self.raw[-1]])[0], [])

    def test_fractional_timestamp(self):
        self.raw[0]["time"] += 0.1
        self.assertEqual(_derive_m5_rows(self.raw)[0], [])
