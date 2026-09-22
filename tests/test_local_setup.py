"""Verify the setup script chooses the correct terminal instead of guessing."""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "setup_local.sh"


class SetupDiscoveryTests(unittest.TestCase):
    def test_separate_wine_data_directory_and_ambiguous_terminals(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            install = home / ".mt5" / "drive_c" / "Program Files" / "MetaTrader 5"
            install.mkdir(parents=True)
            (install / "metaeditor64.exe").touch()
            (install / "metaeditor.exe").touch()
            data = home / ".mt5" / "drive_c" / "users" / "amir" / "AppData" / "Roaming" / "MetaQuotes" / "Terminal" / "alpha"
            (data / "MQL5" / "Experts").mkdir(parents=True)
            env = os.environ.copy()
            env["HOME"] = str(home)
            for key in ("RAMON_MT5_DIR", "RAMON_MT5_DATA_DIR", "RAMON_WINEPREFIX"):
                env.pop(key, None)

            def diagnose() -> subprocess.CompletedProcess[str]:
                return subprocess.run(
                    ["bash", str(SCRIPT), "--diagnose"],
                    env=env, text=True, capture_output=True, check=False,
                )

            found = diagnose()
            self.assertEqual(found.returncode, 0, found.stderr)
            self.assertIn(str(install / "metaeditor64.exe"), found.stdout)
            self.assertIn(str(data), found.stdout)

            other = data.with_name("beta")
            (other / "MQL5" / "Experts").mkdir(parents=True)
            ambiguous = diagnose()
            self.assertNotEqual(ambiguous.returncode, 0)
            self.assertIn("2 MT5 data folder candidate(s)", ambiguous.stderr)

            env["RAMON_MT5_DATA_DIR"] = str(data)
            selected = diagnose()
            self.assertEqual(selected.returncode, 0, selected.stderr)
            self.assertIn(str(data), selected.stdout)


if __name__ == "__main__":
    unittest.main()
