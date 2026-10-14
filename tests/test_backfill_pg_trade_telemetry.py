"""Pure-logic tests for guarded telemetry-only backfill."""
import pytest
from scripts.backfill_pg_trade_telemetry import build_patch


def test_only_missing_fields_backfilled():
    src = {"entry_ea_version": "0.58.3", "trade_role": "MAIN",
           "risk_budget_units": 6.0, "entry_source": None}
    pg = {"entry_ea_version": "0.58.3", "trade_role": None, "other": "preserve"}
    assert build_patch(src, pg) == {"trade_role": "MAIN", "risk_budget_units": 6.0}


def test_conflicting_field_refused():
    with pytest.raises(ValueError, match="conflicting"):
        build_patch({"entry_source": "HUMAN_ASSISTED"},
                    {"entry_source": "AUTO_RAMON"})


def test_no_source_value_never_erases_pg():
    assert build_patch({"entry_source": None}, {"entry_source": "AUTO_RAMON"}) == {}
