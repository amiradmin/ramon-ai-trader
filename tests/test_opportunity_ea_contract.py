"""Static contract tests tying Python structure execution to the MT5 primary EA."""
from pathlib import Path
from ramon.market_opportunity_engine import Opportunity
from ramon.opportunity_execution import apply_live_opportunity


def test_ea_understands_live_structure_protocol():
    ea = (Path(__file__).resolve().parents[1] / "mt5" / "Ramon.mq5").read_text(encoding="utf-8-sig")
    for marker in ('input bool EnableStructureOpportunity = false;',
                   'JsonNumber(reply,"opportunity_live_selected",opportunity_live_selected)',
                   'target_method=="structure_opportunity_v1"',
                   '&& !live_structure_ai',
                   'Trade.Buy(volume,_Symbol,0.0,stop,target,trade_comment)',
                   'Trade.Sell(volume,_Symbol,0.0,stop,target,trade_comment)'):
        assert marker in ea
    for marker in ("V2ExecBegin(", "V2ExecReserve(", "MaxExecutableRiskUnits()", "NewsGuardEntryBlocked()"):
        assert marker in ea


def test_live_candidate_sets_protocol_markers():
    response = {"decision": "WAIT", "market_direction": "NEUTRAL"}
    setup = Opportunity("BUY", "TREND_PULLBACK", "candidate_only", 4200,4198,4204,2,3)
    assert apply_live_opportunity(response, setup, enabled=True, ea_capable=True,
        fresh_quote=True, assessment={"route":"ALLOW"}, max_spread_ok=True)
    assert response["opportunity_live_selected"] == 1
    assert response["target_method"] == "structure_opportunity_v1"
    assert response["target_direction"] == response["decision"] == "BUY"
    assert response["stop_distance"] == 2
    assert response["target_distance"] == 4
