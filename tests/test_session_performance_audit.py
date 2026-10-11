from ramon.session_performance_audit import memberships, summarize


def test_sessions_follow_dst_and_are_independent():
    assert "LONDON" in memberships(1746104400)  # 2025-05-01 13:00 UTC
    assert "NEW_YORK" in memberships(1746104400)


def test_summary_uses_realized_net_units_only():
    result = summarize([{"opened": 1746104400, "net_units": 4}, {"opened": 1746104400, "net_units": -2}])
    assert result["LONDON"]["profit_factor"] == 2
    assert result["NEW_YORK"]["net_units"] == 2
