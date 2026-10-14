from ramon.market_opportunity_engine import Opportunity
from ramon.opportunity_execution import apply_live_opportunity


def candidate(side="BUY"):
    if side == "BUY":
        return Opportunity("BUY", "TREND_PULLBACK", "candidate_only", 4200, 4198, 4204, 2, 3)
    return Opportunity("SELL", "RANGE_REJECTION", "candidate_only", 4200, 4202, 4196, 2, 3)


def run(candidate_value=None, **changes):
    response = {"decision": "WAIT", "reason": "ai_engine_v2_veto",
                "market_direction": "NEUTRAL"}
    options = {"enabled": True, "ea_capable": True, "fresh_quote": True,
               "assessment": {"route": "ALLOW", "allowed_directions": ["BUY", "SELL"]},
               "max_spread_ok": True}
    options.update(changes)
    selected = apply_live_opportunity(response, candidate_value or candidate(), **options)
    return selected, response


def test_wait_to_buy():
    selected, response = run()
    assert selected and response["decision"] == "BUY"
    assert response["stop_distance"] == 2
    assert response["target_distance"] == 4
    assert response["legacy_target_price"] == 4204


def test_wait_to_sell():
    selected, response = run(candidate("SELL"))
    assert selected and response["decision"] == "SELL"


def test_opt_in_and_capability():
    for changes in ({"enabled": False}, {"ea_capable": False}, {"fresh_quote": False}):
        selected, response = run(**changes)
        assert not selected and response["decision"] == "WAIT"


def test_hazards_and_overrides():
    for changes in ({"assessment": {"route": "WAIT"}}, {"max_spread_ok": False},
                    {"manual_override": True}):
        selected, response = run(**changes)
        assert not selected and response["decision"] == "WAIT"


def test_existing_trade_stays():
    response = {"decision": "SELL", "market_direction": "NEUTRAL"}
    selected = apply_live_opportunity(response, candidate(), enabled=True,
        ea_capable=True, fresh_quote=True, assessment={"route": "ALLOW"},
        max_spread_ok=True)
    assert not selected and response["decision"] == "SELL"


def test_conflicting_direction_blocks():
    response = {"decision": "WAIT", "market_direction": "SELL"}
    selected = apply_live_opportunity(response, candidate(), enabled=True,
        ea_capable=True, fresh_quote=True, assessment={"route": "ALLOW"},
        max_spread_ok=True)
    assert not selected and response["decision"] == "WAIT"
