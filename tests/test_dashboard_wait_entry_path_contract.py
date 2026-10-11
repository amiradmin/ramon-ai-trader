"""Read-only contract checks for documented dashboard WAIT-entry behavior.

These are structural regression checks, not proof of historical broker fills.
"""
from pathlib import Path


EA = Path("mt5/Ramon.mq5")
MONITOR = Path("src/ramon/monitor.py")


def test_dashboard_command_is_explicit_and_anchored_to_model_snapshot():
    source = MONITOR.read_text(encoding="utf-8")
    function = source.split("def queue_manual_entry(", 1)[1].split(
        "\ndef queue_manual_guardian(", 1
    )[0]
    assert "manual_unlocked" in function
    assert "DISCRETIONARY" in function
    assert "RECHECK" in function
    assert "Ramon_ManualEntries.txt" in function
    assert "os.fsync(out.fileno())" in function
    assert "persist_human_assisted_event(" in function


def test_dashboard_selection_can_override_wait_but_needs_command():
    source = EA.read_text(encoding="utf-8")
    on_timer = source.split("void OnTimer()", 1)[1]
    command = on_timer.index("dashboard_command=ReadDashboardManualEntry(")
    manual = on_timer.index("if(dashboard_command>0)", command)
    override = on_timer.index("decision=dashboard_direction;", manual)
    wait_gate = on_timer.index('if(decision=="WAIT" && !small_profit)', override)
    assert command < manual < override < wait_gate
    assert "dashboard_manual_entry=true;" in on_timer[override:wait_gate]
    assert "manual_execution_override=1.0;" in on_timer[override:wait_gate]
    assert "sample_key=dashboard_sample_key;" in on_timer[override:wait_gate]


def test_hard_execution_gates_remain_after_manual_override():
    source = EA.read_text(encoding="utf-8")
    on_timer = source.split("void OnTimer()", 1)[1]
    override = on_timer.index("decision=dashboard_direction;")
    order = on_timer.index("Trade.Buy(volume,_Symbol", override)
    path = on_timer[override:order]
    for required in (
        "if(!EnableLiveTrading)",
        "MaxDashboardOpportunityPositions",
        "ACCOUNT_MARGIN_MODE_RETAIL_HEDGING",
        "OrderCalcProfit(",
        "MaxExecutableRiskUnits()",
        "OrderCalcMargin(",
        "V2ExecBegin(",
        "V2Preflight(",
        "V2ExecReserve(",
        "LiveExecutionReady(",
        "AccountLossLimitsBlocked(",
    ):
        assert required in path, required


def test_manual_comment_classifier_does_not_claim_old_labels():
    source = EA.read_text(encoding="utf-8")
    assert 'StringFind(opening_comment,":M")>=0 ? "HUMAN_ASSISTED"' in source
    assert '":M" : ""' in source
