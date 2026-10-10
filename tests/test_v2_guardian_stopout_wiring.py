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
    segment=code[start:start+1100]
    assert "DEAL_REASON_SO" in segment
    assert "GlobalVariableSet(V2StopoutKey()" in segment
    assert "GlobalVariablesFlush()" in segment
