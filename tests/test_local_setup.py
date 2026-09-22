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


def test_metaeditor_discovery_preserves_windows_filename_casing(tmp_path):
    install = tmp_path / ".mt5" / "drive_c" / "Program Files" / "MetaTrader 5"
    (install / "MQL5" / "Experts").mkdir(parents=True)
    env = os.environ.copy()
    env["HOME"] = str(tmp_path)
    for key in ("RAMON_MT5_DIR", "RAMON_MT5_DATA_DIR", "RAMON_WINEPREFIX"):
        env.pop(key, None)
    for filename in ("MetaEditor64.exe", "METAEDITOR64.EXE", "MetaEditor.exe"):
        editor = install / filename
        editor.touch()
        for explicit in (False, True):
            if explicit:
                env["RAMON_MT5_DIR"] = str(install)
            else:
                env.pop("RAMON_MT5_DIR", None)
            result = subprocess.run(["bash", str(SCRIPT), "--diagnose"], env=env,
                                    capture_output=True, text=True)
            assert result.returncode == 0, result.stderr
            assert f"MetaEditor: {editor}\n" in result.stdout
            assert "cannot open" not in result.stdout
        editor.unlink()
    # Presence of a 32-bit filename must not hide the mixed-case 64-bit one.
    (install / "metaeditor.exe").touch()
    (install / "MetaEditor64.exe").touch()
    result = subprocess.run(["bash", str(SCRIPT), "--diagnose"], env=env,
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert f"MetaEditor: {install / 'MetaEditor64.exe'}\n" in result.stdout


def test_compile_invokes_the_discovered_existing_editor(tmp_path):
    install = tmp_path / ".mt5" / "drive_c" / "Program Files" / "MetaTrader 5"
    (install / "MQL5" / "Experts").mkdir(parents=True)
    editor = install / "MetaEditor64.exe"
    editor.touch()
    executables = tmp_path / "bin"
    executables.mkdir()
    wine = executables / "wine"
    wine.write_text('''#!/usr/bin/env bash
set -euo pipefail
[[ -f "$1" && "$1" == */MetaEditor64.exe ]] || exit 72
source_file="${2#/compile:}"
[[ -f "$source_file" ]] || exit 73
printf 'test artifact\n' > "${source_file%.mq5}.ex5"
''')
    wine.chmod(0o755)
    winepath = executables / "winepath"
    winepath.write_text('#!/usr/bin/env bash\nprintf "%s\\n" "$2"\n')
    winepath.chmod(0o755)
    env = os.environ.copy()
    env["HOME"] = str(tmp_path)
    env["PATH"] = str(executables) + os.pathsep + env["PATH"]
    for key in ("RAMON_MT5_DIR", "RAMON_MT5_DATA_DIR", "RAMON_WINEPREFIX"):
        env.pop(key, None)
    result = subprocess.run(["bash", str(SCRIPT), "--mt5-only"], env=env,
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert f"MetaEditor: {editor}\n" in result.stdout
    assert "Ready:" in result.stdout
