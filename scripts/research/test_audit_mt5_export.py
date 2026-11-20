import tempfile
import unittest
from pathlib import Path
from audit_mt5_export import audit


class AuditExportTests(unittest.TestCase):
    def test_invalid_duplicate_and_reversed_rows(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'bars.csv'
            path.write_text('2026.01.01 00:00,2,3,1,2,4,0\n'
                            '2026.01.01 00:15,2,1,1,2,4,0\n'
                            '2026.01.01 00:00,2,3,1,2,4,0\n', encoding='utf-16')
            result = audit(path, 15)
            self.assertEqual(result['rows'], 3)
            self.assertEqual(result['invalid_rows'], 1)
            self.assertEqual(result['duplicate_timestamps'], 1)
            self.assertEqual(result['non_increasing'], 1)
            self.assertEqual(result['timestamp_timezone'], 'UNVERIFIED_BROKER_SERVER_TIME')


if __name__ == '__main__':
    unittest.main()
