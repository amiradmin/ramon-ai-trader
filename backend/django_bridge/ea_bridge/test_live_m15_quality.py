"""Live M15 readiness tests: no broker or database needed."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from django.test import SimpleTestCase

from .strategy_worker import live_m15_quality


class LiveM15QualityTests(SimpleTestCase):
    def setUp(self):
        self.latest = datetime(2026, 11, 23, 9, 0, tzinfo=timezone.utc)
        self.now = self.latest + timedelta(minutes=15, seconds=2)
        self.rows = [SimpleNamespace(timestamp=self.latest - timedelta(minutes=15 * (127 - i)))
                     for i in range(128)]

    def test_valid_contiguous_closed_candles(self):
        self.assertEqual(live_m15_quality(self.rows, now=self.now), 'READY')

    def test_missing_candle_rejected(self):
        self.rows[47].timestamp -= timedelta(minutes=15)
        self.assertEqual(live_m15_quality(self.rows, now=self.now), 'M15_GAPS_OR_DUPLICATES')

    def test_duplicate_candle_rejected(self):
        self.rows[47].timestamp = self.rows[46].timestamp
        self.assertEqual(live_m15_quality(self.rows, now=self.now), 'M15_GAPS_OR_DUPLICATES')

    def test_off_grid_rejected(self):
        self.rows[47].timestamp += timedelta(seconds=1)
        self.assertEqual(live_m15_quality(self.rows, now=self.now), 'M15_OFF_GRID')

    def test_stale_candle_rejected(self):
        self.assertEqual(live_m15_quality(self.rows, now=self.now + timedelta(minutes=16)), 'M15_STALE')

    def test_insufficient_warmup_rejected(self):
        self.assertEqual(live_m15_quality(self.rows[:127], now=self.now), 'M15_WARMUP')
