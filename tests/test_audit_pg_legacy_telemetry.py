"""No database needed: verify legacy telemetry audit never invents values."""
from scripts.audit_pg_legacy_telemetry import compare


def test_missing_fields_distinct_from_conflicts():
    source = {"t1": {"entry_ea_version": "0.58.3", "entry_source": "DASHBOARD_OPPORTUNITY",
                      "trade_role": "MAIN"},
              "t2": {"entry_ea_version": "0.58.4", "entry_source": None, "trade_role": "MAIN"}}
    target = {"t1": {"telemetry": {"entry_ea_version": "0.58.3", "trade_role": "SMALL"}},
              "t2": {"telemetry": {}}}
    stats, conflicts = compare(source, target)
    assert stats["entry_ea_version"]["equal"] == 1
    assert stats["entry_ea_version"]["recoverable_if_verified"] == 1
    assert stats["entry_source"]["recoverable_if_verified"] == 1
    assert stats["entry_source"]["sqlite_null"] == 1
    assert stats["trade_role"]["conflict"] == 1
    assert conflicts[0]["field"] == "trade_role"
