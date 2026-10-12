"""Guardrails for the narrowly scoped test-lock release generator."""
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from generate_cent_lock_release import create_release


def test_release_is_bound_flat_disarmed_reviewed_and_order_free():
    code = create_release(89166482, "LiteFinance-MT5-Live", 1791648001)
    assert "const long expected_login=89166482;" in code
    assert 'const string expected_server="LiteFinance-MT5-Live";' in code
    assert "const long expected_lock_value=1791648001;" in code
    assert 'OperatorConfirmation!=required_confirmation' in code
    delete_at = code.index("GlobalVariableDel(key)")
    for prerequisite in [
        "TERMINAL_TRADE_ALLOWED",
        "PositionsTotal()!=0 || OrdersTotal()!=0",
        "HistorySelect(0,TimeCurrent())",
        "reason==DEAL_REASON_SO",
        "GlobalVariableGet(key)!=(double)expected_lock_value",
    ]:
        assert code.index(prerequisite) < delete_at
    for forbidden in ["OrderSend(", "OrderSendAsync(", "CTrade", "Trade.", "PositionClose(", "OrderClose(", "#import", "#include"]:
        assert forbidden not in code


def test_release_rejects_invalid_identity_or_lock_value():
    with pytest.raises(ValueError):
        create_release(0, "LiteFinance-MT5-Live", 1)
    with pytest.raises(ValueError):
        create_release(1, 'bad"server', 1)
    with pytest.raises(ValueError):
        create_release(1, "Live", 0)
