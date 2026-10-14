from ramon.prevention_plan import prevention_plan

def test_empty_evidence_never_enables_live_changes():
    p=prevention_plan({"cases":[]},[])
    assert p["mode"]=="SHADOW_ONLY"
    assert p["live_changes"]==0
    assert p["proposals"][0]["status"]=="COLLECT_DATA"

def test_repeated_exit_yields_audit_only():
    losses=[{"exit_reason":"DEAL_REASON_SL","net_units":-3},
            {"exit_reason":"DEAL_REASON_SL","net_units":-4}]
    p=prevention_plan({"cases":[]},losses)
    assert p["proposals"][0]["id"]=="LOSS_EXIT_REVIEW"
    assert p["proposals"][0]["live_enabled"] is False

def test_wait_replay_requires_minimum_resolved_cases():
    cases=[{"reason":"timing","status":"SIMULATED_TP"} for _ in range(7)]
    assert prevention_plan({"cases":cases},[])["proposals"][0]["id"]=="INSUFFICIENT_EVIDENCE"
    cases.append({"reason":"timing","status":"SIMULATED_SL"})
    p=prevention_plan({"cases":cases},[])
    assert p["proposals"][0]["id"]=="WAIT_REASON_REVIEW"
    assert p["evaluation_protocol"]["current_status"]=="NOT_VALIDATED"
