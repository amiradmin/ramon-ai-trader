"""Source-level regression checks: protect both MQL5 entry points until MetaEditor CI exists.

These are NOT MQL5 compilation tests, nor proof of live terminal readiness.
"""
from pathlib import Path


EA = Path(__file__).resolve().parents[1] / "mt5" / "Ramon.mq5"


def test_account_wide_guard_placed_in_both_entry_routes():
    text = EA.read_text(encoding="utf-8-sig")
    assert "input bool EnableV2AccountRiskGuard = true;" in text
    assert "bool V2Preflight(" in text
    assert text.count("V2Preflight(") == 3  # definition + both call sites
    primary = text.index('StatusLine="V2 ENTRY BLOCKED:')
    primary_order = text.index('bool submitted=(', primary)
    assert primary < primary_order
    guardian = text.index('StatusLine="GUARDIAN CLOSED: V2 ')
    guardian_order = text.index('bool submitted=direction=="BUY" ? Trade.Buy(', guardian)
    assert guardian < guardian_order


def test_entire_account_and_pending_orders_are_inspected():
    text = EA.read_text(encoding="utf-8-sig")
    assert "for(int i=0;i<PositionsTotal();i++)" in text
    assert "for(int i=0;i<OrdersTotal();i++)" in text
    assert "OrderCalcProfit(position_side,sym,lots,open_price,sl,risk_pnl)" in text
    assert "OrderCalcProfit(pending_side,sym,lots,open_price,sl,risk_pnl)" in text
    assert "OrderCalcMargin(side,_Symbol,volume,entry,extra_margin)" in text
    assert "GlobalVariableSet(V2StopoutKey()" in text
    assert "GlobalVariablesFlush();" in text
