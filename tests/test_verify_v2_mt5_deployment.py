"""Deployment inspection tests, without MT5 connection or order API calls."""
from pathlib import Path
import importlib.util


def load_checker():
    path = Path(__file__).resolve().parents[1] / "scripts/verify_v2_mt5_deployment.py"
    spec = importlib.util.spec_from_file_location("verify_v2_mt5_deployment", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_accepts_identical_source_and_newer_compiled_file(tmp_path):
    checker = load_checker()
    repo = tmp_path / "repo"
    experts = tmp_path / "experts"
    (repo / "mt5").mkdir(parents=True)
    experts.mkdir()
    source = b"EnableV2AccountRiskGuard\nV2Preflight(\nV2StopoutKey()\n"
    (repo / "mt5/Ramon.mq5").write_bytes(source)
    (experts / "Ramon.mq5").write_bytes(source)
    (experts / "Ramon.ex5").write_bytes(b"FAKE COMPILED CONTENT")
    result = checker.inspect(repo, experts)
    assert result["ready_for_tester_review"]
    assert not result["live_trading_authorized"]


def test_rejects_outdated_ex5_and_mismatched_source(tmp_path):
    checker = load_checker()
    repo = tmp_path / "repo"
    experts = tmp_path / "experts"
    (repo / "mt5").mkdir(parents=True)
    experts.mkdir()
    (experts / "Ramon.ex5").write_bytes(b"old compiled image")
    (repo / "mt5/Ramon.mq5").write_text("new source")
    (experts / "Ramon.mq5").write_text("old source")
    result = checker.inspect(repo, experts)
    assert not result["ready_for_tester_review"]
    assert "installed_source_differs_from_git" in result["errors"]
    assert "compiled_binary_older_than_source" in result["errors"]
