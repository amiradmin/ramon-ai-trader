"""Explicit, opt-in executable reversal route. No order submission here."""
from .core import Decision, Settings


def apply_reversal(response: dict, base: Decision, settings: Settings,
                   assessment: dict, *, enabled: bool, manual_overrides=frozenset()) -> bool:
    response["reversal_execution"] = 0
    response["reversal_live_enabled"] = int(enabled)
    if (not enabled or manual_overrides or response.get("decision") != "WAIT"
            or response.get("reason") != "trend_conflict" or response.get("ensemble_active", 0)):
        return False
    if base.reason != "trend_conflict" or base.trend_conflict_active != 1:
        return False
    side = "BUY" if base.buy_edge > base.sell_edge else "SELL"
    # Only replace the directional trend veto, never a hazard/range veto.
    opposing_states = ({"TREND_DOWN", "PULLBACK_DOWN", "BREAKOUT_DOWN", "BREAKOUT_RETEST_DOWN"}
                       if side == "BUY" else
                       {"TREND_UP", "PULLBACK_UP", "BREAKOUT_UP", "BREAKOUT_RETEST_UP"})
    if assessment.get("route") != "CONFIRMED_MODEL" or assessment.get("state") not in opposing_states:
        return False
    if not (max(base.buy_edge, base.sell_edge) >= base.minimum_edge
            and base.signal_strength >= settings.minimum_strength
            and base.intrabar_confirmed == 1 and base.ai_trend_confirmed == 1
            and base.intrabar_direction == side and base.ai_trend_direction == side
            and base.intrabar_move_atr < settings.maximum_entry_extension_atr
            and base.stop_distance > 0 and base.target_distance > 0):
        return False
    response.update(decision=side, reason="confirmed_countertrend_reversal",
                    edge=max(base.buy_edge, base.sell_edge), range_execution=0,
                    reversal_execution=1, market_state=assessment["state"],
                    market_state_policy=assessment["version"],
                    market_state_route="CONFIRMED_REVERSAL")
    return True
