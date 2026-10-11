from pathlib import Path

import pytest

from ramon.hf_cent_sandbox import audit, validate_features


def test_feature_contract_preserves_order_and_rejects_incomplete_or_nonfinite():
    order = [f"feature_{i}" for i in range(300)]
    values = {name: i for i, name in enumerate(reversed(order))}
    assert validate_features(order, values)[0] == 299
    with pytest.raises(ValueError):
        validate_features(order, {**values, "extra": 1})
    with pytest.raises(ValueError):
        validate_features(order, {**values, order[0]: float("nan")})
    with pytest.raises(ValueError):
        validate_features(order, {**values, order[0]: True})


def test_unavailable_model_never_enables_execution(tmp_path):
    result = audit(tmp_path)
    assert result["execution_enabled"] is False
    assert len(result["missing_files"]) == 6
    assert result["status"] == "BLOCKED_UNVERIFIED_FEATURE_PIPELINE"


def test_collector_has_no_trade_or_production_lock_mutation():
    source = (Path(__file__).resolve().parents[1] / "mt5/RamonHFCentShadow.mq5").read_text()
    for forbidden in ("OrderSend", "CTrade", "GlobalVariableDel", "GlobalVariableSet", "#import"):
        assert forbidden not in source
    assert "PERIOD_M15,1," in source
