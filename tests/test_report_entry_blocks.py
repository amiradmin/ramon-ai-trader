from scripts.report_entry_blocks import summarize


def test_counts_state_changes_not_total_decisions():
    model = """RAMON_STRUCTURE setup=NONE candidate=WAIT final=BUY reason=another_execution_lane
RAMON_STRUCTURE setup=NONE candidate=WAIT final=WAIT reason=anomaly_veto
RAMON_STRUCTURE setup=TREND_PULLBACK candidate=BUY final=WAIT reason=ea_not_ready_or_stale_quote"""
    ea = """Ramon 2026.10.14 09:45:00 UTC WAIT moment_anomaly_veto median=4141.51
Ramon 2026.10.14 09:45:00 UTC WAIT ai_engine_v2_timing_not_ready median=4141.51"""
    report = summarize(model, ea)
    assert report["structure_state_changes"] == 3
    assert report["structure_candidate_state_changes"] == {"WAIT":2,"BUY":1}
    assert report["structure_gate_state_changes"]["anomaly_veto"] == 1
    assert report["ea_reasons"]["moment_anomaly_veto"] == 1
    assert report["ea_decision_log_lines"] == 2


def test_empty_logs_are_empty():
    report=summarize("", "")
    assert report["structure_state_changes"] == 0
    assert report["ea_decision_log_lines"] == 0
