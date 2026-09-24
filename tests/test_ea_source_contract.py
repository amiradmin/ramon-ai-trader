from __future__ import annotations

import re
from pathlib import Path


EA = Path(__file__).parents[1] / "mt5" / "Ramon.mq5"


def source() -> str:
    return EA.read_text()


def test_ea_030_keeps_sizing_telemetry_observational():
    text = source()
    assert '#property version "0.30"' in text
    assert 'EA version: 0.30' in text
    assert 'RAMON AI TRADER  v0.30' in text
    # Telemetry staging is deliberately not a trade gate.
    assert 'if(!StageEntrySizing' not in text
    assert re.search(
        r'StageEntrySizing\(LastSampleKey,side,entry,stop,volume\);\s*'
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
    assert 'if(decision=="WAIT" || !EnableLiveTrading)' in text
    assert 'if(LastEntrySignalBar==bar_time)' in text
    assert 'if(today<0 || today>=MaxTradesPerDay)' in text
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
