import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from stage_litefinance_export import convert, stage


class StageTests(unittest.TestCase):
    def test_seasonal_offsets_and_transition_rejected(self):
        self.assertEqual(convert(datetime(2024, 1, 1, 12))[1], 2)
        self.assertEqual(convert(datetime(2024, 6, 1, 12))[1], 3)
        with self.assertRaises(ValueError):
            convert(datetime(2024, 10, 27, 12))

    def test_staging_preserves_raw_and_rejects_existing_destination(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'in.csv'
            output = Path(folder) / 'out.csv'
            source.write_text('2026.01.02 01:15,2,3,1,2,4,0\n', encoding='utf-16')
            result = stage(source, output, 15)
            self.assertEqual(result['output_rows'], 1)
            self.assertIn('2026-01-01T23:15:00+00:00', output.read_text())
            with self.assertRaises(ValueError):
                stage(source, output, 15)
            self.assertTrue(output.exists())
