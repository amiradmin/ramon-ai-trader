from __future__ import annotations

import re
from pathlib import Path


EA = Path(__file__).parents[1] / "mt5" / "Ramon.mq5"


def source() -> str:
    return EA.read_text()


def test_ea_038_keeps_sizing_telemetry_observational():
    text = source()
    assert '#property version "1.630"' in text
    assert 'EA version: 0.63' in text
    assert 'RAMON AI TRADER  v0.63' in text
    assert 'version="0.63";' in text
    # Telemetry staging is deliberately not a trade gate.
    assert 'if(!StageEntrySizing' not in text
    assert re.search(
        r'StageEntrySizing\(LastSampleKey,side,entry,stop,volume\);\s*'
        r'if\(!small_profit\)\s*'
        r'PersistTPPlan\(LastSampleKey,decision,entry,LastTargetTP1,LastTargetTP2,LastTargetTP3\);\s*'
        r'// The broker owns SL/TP immediately',
        text,
    )


def test_ea_persists_sizing_by_exact_sample_key_before_closed_trade_upload():
    text = source()
    for field in (
        'risk_budget_units',
        'planned_volume',
        'min_lot_sl_units',
        'min_lot_override_used',
        'max_executable_risk_usd',
        'money_units_per_usd',
    ):
        assert f'\\"{field}\\"' in text
    assert 'FileWrite(\n         file,"v2"' in text
    assert 'sizing_sample==sample' in text
    assert 'deal_sample==PendingSizingSampleKey' in text


def test_ea_execution_invariants_remain_model_and_hard_cap_guarded():
    text = source()
    assert 'if(decision=="WAIT" && !small_profit)' in text
    assert 'if(!EnableLiveTrading)' in text
    assert 'else if(LastEntrySignalBar==bar_time)' in text
    assert 'if(!SmallOnlyMode && (today<0 || today>=MaxTradesPerDay))' in text
    assert 'if(volume<=0.0)' in text
    assert 'TRADE BLOCKED: min lot > hard risk cap' in text
    assert 'if(!AllowMinLotRiskOverride || MaxExecutableRiskUSD<EffectiveRiskPerTradeUSD()' in text
    assert '|| -money>hard_cap+0.00001)' in text
    assert 'double budget=EffectiveRiskPerTradeUSD()*MoneyUnitsPerUSD;' in text
    assert 'risk_multiplier<0.50 || risk_multiplier>1.50' in text
    assert 'ManagedPosition(ticket,opened)' in text
    assert 'OtherPositionOnSymbol()' in text


def test_fast_snapshot_uses_successful_model_responses_and_preserves_exit_confirmation():
    text = source()
    assert 'SnapshotIntervalSeconds<5' in text
    assert 'EventSetTimer(5)' in text
    assert 'LastDecisionSuccessTime=TimeCurrent();' in text
    assert 'const int WeakConfirmationIntervalSeconds = 30;' in text
    for name in ('TPStageLastDecisionTime', 'MainFastProfitLastDecisionTime',
                 'EarlyAdverseLastDecisionTime'):
        assert f'LastDecisionSuccessTime-{name}>=WeakConfirmationIntervalSeconds' in text
    assert 'if(!weak)\n      {\n         EarlyAdverseWeakSnapshots=0;' in text
    assert 'if(!weak)\n      {\n         MainFastProfitWeakSnapshots=0;' in text
    assert 'LastDecisionSuccessTime>=TPStageHitTime' in text
    assert '&& fresh && TPStageWeakSnapshots>=TPStageWeakSnapshotsRequired' in text


def test_deal_telemetry_reader_is_backward_compatible():
    text = source()
    assert '(marker=="v1" || marker=="v2")' in text
    assert 'if(marker!="v2")' in text


def test_shared_account_risk_is_checked_under_lock_before_any_order() -> None:
    ea = source()
    timer = ea.split("void OnTimer()", 1)[1].split("void OnTradeTransaction(", 1)[0]
    acquire = timer.index("AcquireSharedRiskLock(risk_lease)")
    audit = timer.index("SharedRiskAllowsEntry(side,entry,stop,volume,shared_risk_reason)")
    order = timer.index("Trade.Buy(volume,_Symbol")
    release = timer.index("ReleaseSharedRiskLock(risk_lease);", order)
    assert acquire < audit < order < release
    guard = ea.split("bool SharedRiskAllowsEntry(", 1)[1].split("string EffectiveDiagnosticFileName", 1)[0]
    assert "OrdersTotal()" in guard and "PositionsTotal()" in guard
    assert "magic==PrimaryMagicNumber || magic==SmallProfitMagicNumber" in guard
    assert "MaxCombinedOpenRiskUSD*MoneyUnitsPerUSD" in guard


def test_peak_recovery_is_per_position_and_saved_before_giveback_trigger() -> None:
    ea = source()
    observe = ea.split("void ObserveOpenPositionProfit(", 1)[1].split("void ManageOpenPosition()", 1)[0]
    assert "POSITION_IDENTIFIER" in observe
    assert observe.index("GlobalVariableGet(key)") < observe.index("ProfitProtectionCurrentUnits=PositionGetDouble")
    assert observe.index("GlobalVariablesFlush()") < observe.index("ProfitProtectionArmed=")

