import json

from ramon.profitability import entry_audit, metrics, readiness, role


def trades():
    rows = []
    for day in range(25):
        for j, name in enumerate(("MAIN", "MAIN", "SMALL", "SMALL")):
            net = 2.0 if j % 2 == 0 else -.5
            rows.append(dict(trade_key=f"t{day}-{j}", opened=1800000000 + day*86400+j*100,
                closed=1800000050 + day*86400+j*100, direction="BUY", net_units=net,
                net_r=net, initial_risk_units=1., planned_volume=.01,
                profit_units=net, commission_units=0., swap_units=0., fee_units=0.,
                opened_utc_offset_seconds=0, closed_utc_offset_seconds=0, money_units_per_usd=100.,
                max_executable_risk_usd=.02, trade_role=name,
                entry_magic=26092212 if name == "MAIN" else 26092213,
                training_status="LEARNABLE", entry_ea_version="0.60", chronos_model="fixed-model",
                bundle_id="", model_metadata=json.dumps({"chronos_revision":"revision-a",
                "strategy_code_fingerprint":"code-a", "execution_profile":{"role":name,"risk_usd":.06},
                "decision_audit":{"settings":{"horizon":4},"base":{"intrabar_confirmed":1,
                "intrabar_direction":"BUY","ai_trend_confirmed":1,"ai_trend_direction":"BUY"}},
                "news_live_context":{"news_source_ready":1}}),
                news_features=json.dumps({"high_impact_near":0}),
                regime_features=json.dumps({"ret_1_atr":1,"ret_4_atr":1,"ret_12_atr":1})))
    return rows


def check(rows):
    return readiness(rows, since_utc=1800000000, version="0.60", capital_usd=30.)


def test_positive_fixed_forward_cohort_still_requires_contract_review():
    result = check(trades())
    assert all(result["checks"].values())
    assert result["status"] == "EVIDENCE_PASSED_CONTRACT_REVIEW_REQUIRED"
    assert result["active_utc_days"] == 25
    assert result["roles"]["MAIN"]["profiles"] == 1


def test_empty_and_old_versions_cannot_pass():
    assert check([])["status"] == "NOT_READY"
    rows = trades()
    for r in rows:
        r["entry_ea_version"] = "0.57"
    assert check(rows)["summary"]["trades"] == 0


def test_missing_conversion_never_becomes_zero_or_assumed_cent():
    rows = trades()
    rows[0]["money_units_per_usd"] = None
    result = check(rows)
    assert not result["checks"]["usd_conversion_complete"]
    assert result["summary"]["usd_coverage"] == 99
    assert result["summary"]["net_units"] is None


def test_mixed_cent_and_dollar_units_not_aggregated_for_profit_factor():
    rows = trades()
    rows[0]["money_units_per_usd"] = 1
    assert metrics(rows)["profit_factor"] is None


def test_retained_input_change_invalidates_fixed_profile():
    rows = trades()
    meta = json.loads(rows[-1]["model_metadata"])
    meta["execution_profile"]["risk_usd"] = .10
    rows[-1]["model_metadata"] = json.dumps(meta)
    assert not check(rows)["checks"]["each_role_positive_and_fixed"]


def test_missing_clock_cost_reconciliation_and_risk_overshoot_fail():
    rows = trades()
    rows[0]["opened_utc_offset_seconds"] = None
    rows[1]["fee_units"] = -1
    rows[2]["initial_risk_units"] = 3
    checks = check(rows)["checks"]
    assert not checks["timestamps_complete"]
    assert not checks["fees_reconciled"]
    assert not checks["recorded_per_trade_caps_respected"]


def test_role_conflict_is_unknown_not_reclassified_by_current_chart():
    row = trades()[0]
    row["entry_magic"] = 26092213
    assert role(row) == "UNKNOWN"


def test_fixed_entry_screens_have_separate_later_period_and_no_promotion():
    result = entry_audit(trades())
    assert not result["live_changes"]
    assert result["periods"]["later_30_percent"]["screens"]["both_direction_confirmations"]["trades"] == 30
    assert result["periods"]["later_30_percent"]["screens"]["aligned_15_60_180_minute_returns"]["trades"] == 30
    assert result["periods"]["later_30_percent"]["screens"]["confirmed_news_clear"]["trades"] == 30
