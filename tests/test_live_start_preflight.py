from pathlib import Path

from ramon.live_start_preflight import evaluate_diagnostic, run


READY = """Market: OPEN/FULL  TerminalConnected: YES
Trade permissions: terminal=YES ea=YES account=YES
Live: ARMED  AccountLock: OK
AccountType: CENT  MoneyUnitsConfirmed: YES
News: CLEAR  SourceReady: YES  ModelReady: YES
Status: Waiting
RiskGate: PASS: preferred risk budget
"""


def test_diagnostic_requires_every_execution_precondition():
    checks = evaluate_diagnostic(READY)
    assert all(checks.values())
    checks = evaluate_diagnostic(READY.replace("TerminalConnected: YES", "TerminalConnected: NO"))
    assert checks["terminal_connected"] is False


def test_missing_diagnostic_fails_closed(tmp_path: Path):
    result = run(tmp_path / "absent.txt", model_health_url="http://127.0.0.1:1/health", max_age_seconds=60)
    assert result["ready"] is False
    assert result["failed"] == ["diagnostic_exists"]


def test_stale_tick_is_not_start_ready():
    checks = evaluate_diagnostic(READY.replace("Status: Waiting", "Status: Stale tick"))
    assert checks["fresh_tick"] is False
