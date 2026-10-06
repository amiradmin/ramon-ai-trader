from __future__ import annotations

import re
from pathlib import Path


EA = Path(__file__).parents[1] / "mt5" / "Ramon.mq5"


def source() -> str:
    return EA.read_text()


def test_current_ea_keeps_sizing_telemetry_observational():
    text = source()
    assert '#property version "1.590"' in text
    assert '#define RAMON_EA_VERSION "0.59.0"' in text
    assert '+"EA version: "+RAMON_EA_VERSION+' in text
    assert '"RAMON AI TRADER  v"+RAMON_EA_VERSION+' in text
    assert 'version=RAMON_EA_VERSION;' in text
    # Telemetry staging is deliberately not a trade gate.
    assert 'if(!StageEntrySizing' not in text
    assert re.search(
        r'StageEntrySizing\(LastSampleKey,side,entry,stop,volume\);\s*'
        r'if\(!small_profit && !range_trade\)\s*'
        r'PersistTPPlan\(LastSampleKey,decision,entry,stop,\s*'
        r'LastTargetTP1,LastTargetTP2,LastTargetTP3\);\s*'
        r'// The broker owns SL/TP immediately',
        text,
    )


def test_sync_prioritizes_recent_positions_without_increasing_upload_rate():
    text = source().split('void SyncClosedTrades()', 1)[1].split('string TPPlanGlobalKey', 1)[0]
    assert 'for(int i=ArraySize(identifiers)-1;i>=0;i--)' in text
    assert 'now-LastTradeSync<30' in text
    assert 'return; // At most one atomic outbox item per sync cycle; a separate worker sends it.' in text


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


def test_deal_telemetry_reader_is_backward_compatible():
    text = source()
    assert '(marker=="v1" || marker=="v2")' in text
    assert 'if(marker!="v2")' in text


def test_closed_position_runtime_is_cleared_before_rendering():
    for name in ('Ramon.mq5', 'Ramon_installed_early_adverse.mq5'):
        text = (Path(__file__).resolve().parents[1] / 'mt5' / name).read_text()
        render = text.split('void ShowStatus()', 1)[1].split('bool JsonText', 1)[0]
        assert 'if(!ManagedPosition(status_ticket,status_opened))' in render
        for reset in ('ResetProfitProtectionState', 'ResetTPStageRuntime',
                      'ResetEarlyAdverseState', 'ResetMainFastProfitState',
                      'ResetMarketClosedExitPause'):
            assert render.index(reset + '();') < render.index('WriteDiagnostic();')


def test_news_guard_applies_to_normal_and_range_positions_before_entry():
    text = source()
    manager = text.split('void ManageOpenPosition()', 1)[1].split('void OnTimer()', 1)[0]
    assert manager.index('ManageNewsGuard(ticket)') < manager.index('ManageRangeMainPosition(ticket,opened)')
    assert 'delta<=before_minutes*60 && delta>=-15*60' in text
    assert 'NewsGuardWindow(15)' in text
    assert 'NewsGuardWindow(5)' in text
    assert '"news_guard_exit"' in text
    assert 'news_high_event_time' in text


def test_ea_defends_normal_entry_against_older_server_response():
    text=source()
    guard=text.split('bool range_trade=(range_execution>=0.5);',1)[1].split('if(range_trade)',1)[0]
    assert '!range_trade' in guard
    assert 'intrabar_confirmed<0.5 || ai_trend_confirmed<0.5' in guard
    assert 'intrabar_direction!=decision || ai_trend_direction!=decision' in guard
    assert 'return;' in guard


def test_predicted_auto_close_is_ticket_bound_and_tick_driven():
    text = source()
    assert 'Ramon_AutoCloseCommands.txt' in text
    assert 'DashboardAutoCloseKey(requested_ticket)' in text
    assert 'PositionSelectByTicket(requested_ticket)' in text
    assert 'StringFind(comment,"Ramon:"+execution_sample+":M")!=0' in text
    assert 'bool reached=(type==POSITION_TYPE_BUY ? tick.bid>=target : tick.ask<=target);' in text
    assert 'RecordDealTelemetry(Trade.ResultDeal(),"chronos_predicted_auto_close")' in text
    on_tick = text.split('void OnTick()', 1)[1].split('string DashboardAliasFileName', 1)[0]
    assert 'ProcessDashboardPredictedAutoCloseCrossings();' in on_tick


def test_live_direction_ai_can_own_direction_without_legacy_confirmation():
    text = source()
    assert 'double ai_engine_v2_selected=0.0;' in text
    assert 'JsonNumber(reply,"ai_engine_v2_selected",ai_engine_v2_selected)' in text
    assert 'bool live_direction_ai=(ai_engine_v2_selected>=0.5' in text
    assert 'LIVE DIRECTION AI accepted' in text
