"""Read-only source invariants for EA 0.59.9 research coverage."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EA = (ROOT / "mt5/Ramon.mq5").read_text(encoding="utf-8-sig")
RT = (ROOT / "mt5/RamonResearchTelemetry.mqh").read_text(encoding="utf-8")

def test_ea_version_and_session_version_match():
    assert '#property version "1.599"' in EA
    assert '#define RAMON_EA_VERSION "0.59.9"' in EA
    assert r'\"ea_version\":\"0.59.9\"' in RT

def test_all_observability_events_present():
    for name in ("decision_gate", "order_intent", "order_result",
                 "close_intent", "close_result", "position_quote",
                 "protection_change", "deal", "session_start", "session_end"):
        assert f'"{name}"' in EA + RT

def test_trade_policy_calls_still_present_and_logger_is_observational():
    for required in ("V2Preflight(", "V2ExecReserve(", "V2ExecRecordResult(",
                     "Trade.Buy(", "Trade.Sell(", "Trade.PositionClose("):
        assert required in EA
    for forbidden in ("Trade.Buy(", "Trade.Sell(", "Trade.PositionClose(",
                      "OrderSend(", "WebRequest(", "PositionModify("):
        assert forbidden not in RT

def test_no_timer_instrumentation_in_ontick():
    tick = EA.split("void OnTick()", 1)[1].split("\n}", 1)[0]
    assert "RT_Order(" not in tick and "RT_Append(" not in tick
    assert "RT_Gate(" not in tick and "RT_ObservePosition(" not in tick

def test_distinguish_manual_and_automatic_without_guessing():
    assert '"dashboard_manual"' in EA
    assert '"ea_automatic"' in EA
    assert 'external_manual' in RT
    assert 'broker_or_unknown' in RT
    assert 'expert_or_api' in RT
