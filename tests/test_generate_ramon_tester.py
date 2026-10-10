"""Test the isolated DLL-free tester source generator (no broker access)."""
import importlib.util
from pathlib import Path

import pytest


def generator():
    path = Path(__file__).resolve().parents[1] / "scripts/generate_ramon_tester.py"
    spec = importlib.util.spec_from_file_location("generate_ramon_tester", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_generator_removes_dll_and_preserves_risk_guard():
    source = (Path(__file__).resolve().parents[1] / "mt5/Ramon.mq5").read_text()
    output = generator().create_tester_source(source)
    assert '#import "user32.dll"' not in output
    assert '#import "kernel32.dll"' not in output
    assert "OpenClipboard(" not in output
    assert "CopyDiagnosticToClipboard()" in output
    assert 'input bool EnableLiveTrading = false;' in output
    assert "input bool WriteDiagnosticFile = false;" in output
    assert "bool V2Preflight(" in output
    assert output.count("V2Preflight(") == source.count("V2Preflight(")
    assert "Trade.Buy(" in output and "Trade.Sell(" in output


def test_unknown_code_layout_fails_closed():
    with pytest.raises(ValueError):
        generator().create_tester_source("void OnInit() {}")
