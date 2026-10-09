import time
from pathlib import Path

import pytest

from ramon import monitor


SAMPLE = "abcdef1234567890"
EXECUTION = "1234567890abcdef"


def position(tmp_path, monkeypatch, *, profit=-3, state="OFF", legacy=False):
    diagnostic = tmp_path / "Ramon_Diagnostic.txt"
    monkeypatch.setattr(monitor, "read_diagnostic", lambda _: ({"captured_epoch": time.time(), "Managed position": "#42"}, None))
    line = f"{SAMPLE}|{EXECUTION}|BUY|42|1800000000|{profit}|0.01|100|99|0"
    if not legacy:
        line += f"|{state}"
    diagnostic.with_name("Ramon_OpenDashboardPositions.txt").write_text(line + "\n")
    return diagnostic


def test_guardian_queues_exact_execution_ticket_and_allows_cancel(tmp_path, monkeypatch):
    diagnostic = position(tmp_path, monkeypatch)
    result = monitor.queue_manual_guardian(diagnostic, {"sample_key": SAMPLE, "ticket": "42"})
    assert result == {"queued": True, "ticket": "42", "action": "ARM"}
    parts = diagnostic.with_name("Ramon_GuardianCommands.txt").read_text().strip().split("|")
    assert parts[1:] == [SAMPLE, EXECUTION, "42", "ARM"]
    monitor.queue_manual_guardian(diagnostic, {"sample_key": SAMPLE, "ticket": "42", "action": "CANCEL"})
    assert diagnostic.with_name("Ramon_GuardianCommands.txt").read_text().splitlines()[-1].endswith("|42|CANCEL")


@pytest.mark.parametrize("profit,state,legacy", [(0,"OFF",False),(2,"OFF",False),(-3,"USED",False),(-3,"RECOVERY",False),(-3,"OFF",True)])
def test_guardian_rejects_profit_recovery_reuse_and_old_ea(tmp_path, monkeypatch, profit, state, legacy):
    diagnostic = position(tmp_path, monkeypatch, profit=profit, state=state, legacy=legacy)
    with pytest.raises(ValueError):
        monitor.queue_manual_guardian(diagnostic, {"sample_key": SAMPLE, "ticket": "42"})
    assert not diagnostic.with_name("Ramon_GuardianCommands.txt").exists()


@pytest.mark.parametrize("payload", [{"sample_key": SAMPLE,"ticket":"41"}, {"sample_key":"1"*16,"ticket":"42"}, {"sample_key":SAMPLE,"ticket":"42","action":"REVERSE_NOW"}, {"sample_key":SAMPLE,"ticket":"42\n"}])
def test_guardian_rejects_wrong_identity_or_command(tmp_path, monkeypatch, payload):
    diagnostic = position(tmp_path, monkeypatch)
    with pytest.raises(ValueError):
        monitor.queue_manual_guardian(diagnostic, payload)


def test_guardian_cancel_allowed_after_position_recovers(tmp_path, monkeypatch):
    diagnostic = position(tmp_path, monkeypatch, profit=2, state="ARMED")
    assert monitor.queue_manual_guardian(diagnostic, {"sample_key":SAMPLE,"ticket":"42","action":"CANCEL"})["queued"]


def test_native_guardian_closes_before_reversal_and_persists_one_shot():
    # Source contract only; MetaEditor compilation and broker replay are separate.
    source = Path("mt5/Ramon.mq5").read_text()
    guardian = source.split("bool ProcessGuardianReversals()", 1)[1].split("void ProcessGuardianRecoveryExits()", 1)[0]
    assert guardian.index('GlobalVariableSetOnCondition') < guardian.index('Trade.PositionClose') < guardian.index('Trade.Buy')
    assert "PositionSelectByTicket(ticket)" in guardian
    assert "if(count<2) continue;" in guardian
    assert "LastModelSnapshotTime>armed" in guardian
    geometry = source.split("bool GuardianGeometry(",1)[1].split("bool ProcessGuardianReversals()",1)[0]
    assert "volume>original_volume" in geometry
    assert "GuardianMaxRiskUSD,MaxExecutableRiskUSD" in geometry
    assert "AllowMinLotRiskOverride" not in geometry
