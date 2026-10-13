"""Static safety invariants for the restored EA research logger.

MetaEditor compilation and live demo verification are still required separately.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EA = (ROOT / "mt5" / "Ramon.mq5").read_text(encoding="utf-8-sig")
LOGGER = (ROOT / "mt5" / "RamonResearchTelemetry.mqh").read_text(encoding="utf-8")

def test_module_is_observational():
    for forbidden in ("Trade.Buy(", "Trade.Sell(", "Trade.PositionClose(",
                      "OrderSend(", "WebRequest(", "PositionModify("):
        assert forbidden not in LOGGER
    assert "FILE_COMMON" in LOGGER
    assert "CP_UTF8" in LOGGER
    assert 'RamonResearch_' in LOGGER

def test_ea_lifecycle_is_wired():
    assert '#include "RamonResearchTelemetry.mqh"' in EA
    assert "RT_Start(EnableResearchTelemetry" in EA
    assert "RT_Stop(MagicNumber,SmallOnlyMode,reason)" in EA
    assert "RT_Deal(MagicNumber,SmallOnlyMode,trans.deal)" in EA
    assert "RT_ObservePosition(MagicNumber,SmallOnlyMode,LastSampleKey)" in EA

def test_logger_not_in_tick_critical_path():
    tick = EA.split("void OnTick()", 1)[1].split("\n}", 1)[0]
    assert "RT_ObservePosition(" not in tick
    timer = EA.split("void OnTimer()", 1)[1].split("\n}", 1)[0]
    assert "RT_ObservePosition(" in timer

def test_logging_does_not_change_execution_calls():
    assert "V2Preflight(" in EA
    assert "V2ExecReserve(" in EA
    assert "RecordDealTelemetry(trans.deal)" in EA
