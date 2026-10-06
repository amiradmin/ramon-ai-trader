from ramon.server import combined_30m_bias


def test_combined_30m_bias_follows_consensus():
    snapshot = {
        "ai_engine_v2_buy_quality": 0.74,
        "ai_engine_v2_sell_quality": 0.26,
        "market_direction": "BUY",
        "intrabar_confirmed": 1,
        "intrabar_direction": "BUY",
    }
    result = combined_30m_bias("UP", snapshot, snapshot_age_seconds=5)
    assert result["bias_direction"] == "BUY"
    assert result["bias_confidence"] > 0.5


def test_combined_30m_bias_can_disagree_with_chronos():
    snapshot = {
        "ai_engine_v2_buy_quality": 0.18,
        "ai_engine_v2_sell_quality": 0.82,
        "market_direction": "SELL",
        "intrabar_confirmed": 1,
        "intrabar_direction": "SELL",
    }
    result = combined_30m_bias("UP", snapshot, snapshot_age_seconds=5)
    assert result["bias_direction"] == "SELL"


def test_combined_30m_bias_marks_near_balance_mixed():
    snapshot = {
        "ai_engine_v2_buy_quality": 0.50,
        "ai_engine_v2_sell_quality": 0.50,
        "market_direction": "SELL",
        "intrabar_confirmed": 1,
        "intrabar_direction": "BUY",
    }
    result = combined_30m_bias("FLAT", snapshot, snapshot_age_seconds=5)
    assert result["bias_direction"] == "MIXED"


def test_combined_30m_bias_degrades_to_chronos_when_context_stale():
    result = combined_30m_bias("DOWN", {"market_direction": "BUY"}, snapshot_age_seconds=200)
    assert result["bias_direction"] == "DOWN"
    assert result["bias_source"] == "chronos_only_stale_ramon_context"
