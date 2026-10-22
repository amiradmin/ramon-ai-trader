from ramon.direction_conflict_research import classify, summarize

def test_wait_has_priority():
    assert classify("BUY", "WAIT", "SELL") == "WAIT_CONFLICT"

def test_override_is_not_wait():
    assert classify("SELL", "BUY", "BUY") == "DIRECTION_OVERRIDE"

def test_none_direction_is_separate():
    assert classify("NONE", "SELL", "SELL") == "NO_SNAPSHOT_DIRECTION"

def test_report_aggregates_without_changing_rows():
    rows = [
        {"source": "DASHBOARD_OPPORTUNITY", "snapshot": "BUY",
         "final": "WAIT", "executed": "SELL", "net_r": -0.5,
         "quote_time": 1000, "opened": 1009},
        {"source": "DASHBOARD_OPPORTUNITY", "snapshot": "SELL",
         "final": "WAIT", "executed": "BUY", "net_r": 0.2,
         "quote_time": 1100, "opened": 1300},
    ]
    result = summarize(rows)
    assert result == [
        {"source": "DASHBOARD_OPPORTUNITY", "kind": "WAIT_CONFLICT",
         "count": 2, "net_r": -0.3, "within_90_seconds": 1}
    ]
    assert len(rows) == 2
