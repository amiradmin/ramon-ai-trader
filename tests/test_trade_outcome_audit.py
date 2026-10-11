from ramon.trade_outcome_audit import summarize


def test_aggregates_cost_inclusive_r_and_missing_values():
    rows = [
        dict(opened=3600, direction="BUY", net_r=1.5, net_units=15,
             exit_reason="DEAL_REASON_SL", exit_detail=None,
             entry_ea_version="0.5", trade_role="MAIN",
             manual_intervention=None, matched_sample="a", atr=5, spread=.42),
        dict(opened=3900, direction="BUY", net_r=-1, net_units=-10,
             exit_reason="DEAL_REASON_EXPERT", exit_detail="manual_dashboard_close",
             entry_ea_version="0.5", trade_role="MAIN",
             manual_intervention=None, matched_sample="b", atr=None, spread=None),
        dict(opened=7200, direction="SELL", net_r=0, net_units=0,
             exit_reason="DEAL_REASON_EXPERT", exit_detail=None,
             entry_ea_version="0.6", trade_role="MAIN",
             manual_intervention=0, matched_sample=None, atr=8, spread=.3),
    ]
    result = summarize(rows)
    assert result["total_trades"] == 3
    assert result["overall"]["net_r_sum"] == .5
    assert result["overall"]["profit_factor_r"] == 1.5
    assert result["groups"]["entry_hour_utc"]["01"]["trades"] == 2
    assert result["groups"]["manual_close"]["YES"]["net_r_sum"] == -1
    assert result["missing_fields"]["matched_decision"] == 1
    assert result["groups"]["spread_price_bucket"]["MISSING"]["trades"] == 1
