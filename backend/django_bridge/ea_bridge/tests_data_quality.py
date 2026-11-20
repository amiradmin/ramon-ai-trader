from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from django.test import SimpleTestCase
from ea_bridge.management.commands.audit_market_bar_quality import inspect_rows


class DataQualityTests(SimpleTestCase):
    def row(self, timestamp, **kwargs):
        fields = dict(source='broker', symbol='XAUUSD_o', timeframe='M15', timestamp=timestamp,
                      open=Decimal('2'), high=Decimal('3'), low=Decimal('1'), close=Decimal('2'), volume=Decimal('1'))
        fields.update(kwargs)
        return SimpleNamespace(**fields)

    def test_grid_gaps_offsets_and_invalid_ohlc(self):
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        rows = [self.row(start), self.row(start + timedelta(seconds=1)),
                self.row(start + timedelta(minutes=30), high=Decimal('1'))]
        report = inspect_rows(rows)[0]
        self.assertEqual((report['rows'], report['aligned_rows'], report['off_grid_rows']), (3, 2, 1))
        self.assertEqual(report['offset_seconds_counts'], {'1.0': 1})
        self.assertEqual(report['elapsed_grid_slots_without_bar'], 1)
        self.assertEqual(report['invalid_ohlcv_rows'], 1)
        self.assertEqual(report['status'], 'INVALID')

    def test_years_sources_and_duplicate_detection(self):
        start = datetime(2025, 12, 31, 23, 45, tzinfo=timezone.utc)
        rows = [self.row(start), self.row(start), self.row(start + timedelta(minutes=15)),
                self.row(start, source='other')]
        report = inspect_rows(rows)
        self.assertEqual(len(report), 3)
        self.assertEqual(report[0]['duplicate_timestamps'], 1)
        self.assertEqual(len(report[0]['sha256']), 64)
