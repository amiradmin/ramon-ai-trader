"""Static guardrails for Guardian execution ordering and account risk coverage.

Source-level assertions do not constitute broker fill or lifecycle tests.
"""
from pathlib import Path

SOURCE=Path(__file__).resolve().parents[1]/"mt5"/"Ramon.mq5"


def test_guardian_closes_parent_before_reversing_and_rechecks_risk():
    code=SOURCE.read_text(encoding="utf-8-sig")
    section=code[code.index("bool ProcessGuardianReversals()"):code.index("bool ProcessGuardianReversals()")+7500]
    close=section.index("Trade.PositionClose(ticket,MaxDeviationPoints)")
    confirm=section.index("PositionSelectByTicket(ticket)",close)
    preflight=section.index("V2Preflight(recovery_side,recovery_quote,stop,volume,guard_reason)",confirm)
    entry=section.index("Trade.Buy(",preflight)
    assert close<confirm<preflight<entry
    assert 'StatusLine="GUARDIAN CLOSED: V2 "+guard_reason' in section


def test_risk_preflight_does_not_filter_by_magic():
    code=SOURCE.read_text(encoding="utf-8-sig")
    start=code.index("bool V2Preflight(")
    stop=code.index("\nvoid OnTradeTransaction(",start)
    section=code[start:stop]
    assert "PositionsTotal()" in section and "OrdersTotal()" in section
    assert "POSITION_MAGIC" not in section
    assert "ORDER_MAGIC" not in section
    assert "GlobalVariableCheck(V2StopoutKey())" in section


def test_stopout_callback_flushes_terminal_lock():
    code=SOURCE.read_text(encoding="utf-8-sig")
    start=code.index("void OnTradeTransaction(")
    segment=code[start:code.index("bool CloseManagedPositionFromDashboard()",start)]
    assert "DEAL_REASON_SO" in segment
    assert "V2PersistStopout(trans.deal,deal_msc)" in segment
    assert "V2StopoutReviewed(" in segment
    helper=code[code.index("bool V2PersistStopout("):code.index("bool V2RecoverStopoutHistory(")]
    assert "GlobalVariableSet(V2StopoutKey()" in helper
    assert "GlobalVariablesFlush()" in helper
    assert "V2StopoutFaultLatched=true" in helper


def test_stopout_history_is_scanned_at_startup_and_before_optional_risk_guard():
    code=SOURCE.read_text(encoding="utf-8-sig")
    init=code[code.index("int OnInit()"):]
    assert init.index("LockedAccountLogin=current_login") < init.index("V2RecoverStopoutHistory(stopout_reason)")
    preflight=code[code.index("bool V2Preflight("):code.index("void OnTradeTransaction(")]
    assert preflight.index("V2RecoverStopoutHistory(reason)") < preflight.index("if(!EnableV2AccountRiskGuard)")
    history=code[code.index("bool V2RecoverStopoutHistory("):code.index("bool V2Preflight(")]
    assert "HistorySelect(0,now)" in history
    assert "DEAL_REASON_SO" in history and "DEAL_TIME_MSC" in history
    assert "DEAL_MAGIC" not in history and "DEAL_SYMBOL" not in history
    assert "GlobalVariableDel" not in history


def test_known_test_lock_release_is_exact_and_never_waives_a_real_stopout():
    code=SOURCE.read_text(encoding="utf-8-sig")
    start=code.index("bool V2ReleaseKnownTestLock(")
    release=code[start:code.index("bool V2RecoverStopoutHistory(",start)]
    assert "const long expected_login=89166482;" in release
    assert 'const string expected_server="LiteFinance-MT5-Live";' in release
    assert "const double expected_value=1791648001.0;" in release
    delete=release.index("GlobalVariableDel(key)")
    for guard in ["PositionsTotal()!=0 || OrdersTotal()!=0", "HistorySelect(0,now)",
                  "deal_reason==DEAL_REASON_SO", "stored!=expected_value"]:
        assert release.index(guard)<delete
    assert "OrderSend(" not in release and "CTrade" not in release and "Trade." not in release
