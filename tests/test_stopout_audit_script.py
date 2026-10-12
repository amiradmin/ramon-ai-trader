from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "mt5" / "scripts" / "RamonStopoutAudit.mq5"


def test_stopout_audit_is_read_only_and_reports_broker_records():
    code = SOURCE.read_text(encoding="utf-8-sig")
    assert "HistorySelect" in code
    assert "DEAL_REASON_SO" in code
    assert "RAMON_STOP_OUT_AUDIT RECORD" in code
    assert "DEAL_TIME_MSC" in code
    for forbidden in [
        "OrderSend(", "OrderSendAsync(", "CTrade", "Trade.",
        "PositionClose(", "GlobalVariableSet(", "GlobalVariableDel(",
    ]:
        assert forbidden not in code
