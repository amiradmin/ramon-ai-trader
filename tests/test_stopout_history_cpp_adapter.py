"""Runs translated production Stop Out control flow with in-memory API adapters."""
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))
from generate_stopout_history_cpp_adapter import create_cpp_adapter


def test_production_stopout_prefix_runs_all_offline_history_cases(tmp_path):
    compiler = shutil.which('g++')
    if not compiler or not Path('/usr/include/openssl/sha.h').exists():
        pytest.skip('64-bit Linux g++ and OpenSSL development headers required')
    cpp = tmp_path/'history.cpp'
    cpp.write_text(create_cpp_adapter((ROOT/'mt5/Ramon.mq5').read_text(encoding='utf-8-sig')))
    executable = tmp_path/'history'
    build = subprocess.run([compiler, '-std=c++17', str(cpp), '-lcrypto', '-o', str(executable)],
                           capture_output=True, text=True, timeout=30)
    assert build.returncode == 0, build.stderr
    result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.count('RAMON_HISTORY_FIXTURE PASS ') == 18
    assert 'RAMON_HISTORY_FIXTURE FAIL ' not in result.stdout
    assert 'SUMMARY cases=18 failures=0' in result.stdout
