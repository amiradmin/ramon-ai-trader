"""Check DST conversion, incomplete groups and the actual MT5 export."""
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from ea_bridge.mt5_export import aggregate_m15, read_m1, server_to_utc


class MT5ExportTests(unittest.TestCase):
    def test_dst_offsets_and_transition_rejection(self):
        self.assertEqual(server_to_utc(datetime(2026, 10, 23, 15)).hour, 12)
        self.assertEqual(server_to_utc(datetime(2026, 10, 26, 15)).hour, 13)
        with self.assertRaises(ValueError):
            server_to_utc(datetime(2026, 10, 25, 15))

    def test_complete_m15_only_and_duplicate_rejected(self):
        header = "<DATE>\t<TIME>\t<OPEN>\t<HIGH>\t<LOW>\t<CLOSE>\t<TICKVOL>\t<VOL>\t<SPREAD>\n"
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "mt5.csv"
            lines = [f"2026.11.20\t02:{i:02}:00\t100\t101\t99\t100\t2\t0\t26\n" for i in range(30) if i != 20]
            path.write_text(header + "".join(lines))
            bars, report = read_m1(path)
            m15, incomplete = aggregate_m15(bars)
            self.assertEqual((report["rows"], len(m15), incomplete), (29, 1, 1))
            self.assertEqual(m15[0][0], datetime(2026, 11, 20, tzinfo=timezone.utc))
            path.write_text(header + lines[0] + lines[0])
            with self.assertRaisesRegex(ValueError, "duplicate"):
                read_m1(path)


if __name__ == "__main__":
    unittest.main()
