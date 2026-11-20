import tempfile
import unittest
from pathlib import Path
from compare_mt5_timeframes import compare


class ComparisonTests(unittest.TestCase):
    def test_consistent_and_incomplete_bars(self):
        with tempfile.TemporaryDirectory() as folder:
            m15, h1 = Path(folder) / 'm15.csv', Path(folder) / 'h1.csv'
            m15.write_text(''.join(f'2026.01.01 00:{minute:02d},2,3,1,2,1,0\n' for minute in (0,15,30,45)), encoding='utf-16')
            h1.write_text('2026.01.01 00:00,2,3,1,2,4,0\n2026.01.01 01:00,2,3,1,2,4,0\n', encoding='utf-16')
            report = compare(m15, h1)
            self.assertEqual(report['counts']['matching_ohlc'], 1)
            self.assertEqual(report['counts']['incomplete_or_missing_m15'], 1)
            self.assertEqual(report['mismatches'], [])
