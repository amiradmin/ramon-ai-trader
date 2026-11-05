"""Editable, validated strategy thresholds for the Django-owned decision pipeline."""
from copy import deepcopy

# Only strategy thresholds are editable here. Account exposure ceilings, daily-loss
# protection, closed-bar checks, account identity, and execution authorization stay hard guards.
THRESHOLDS = {
    "opportunities": {
        "minimum_score": {"label":"Minimum opportunity score","unit":"0–1","min":0.10,"max":0.95,"step":0.01,"description":"Minimum composite opportunity score required to advance."},
        "adx_trending": {"label":"ADX trending threshold","unit":"ADX","min":5,"max":50,"step":0.5,"description":"ADX level used to classify a directional market."},
        "adx_ranging": {"label":"ADX ranging threshold","unit":"ADX","min":3,"max":45,"step":0.5,"description":"ADX level below which the market can be classified as ranging."},
        "regime_lookback": {"label":"Regime stability lookback","unit":"bars","min":1,"max":50,"step":1,"description":"Number of completed bars used to compare the prior market regime."},
        "min_ema_separation_atr": {"label":"Minimum EMA separation","unit":"ATR","min":0.01,"max":2,"step":0.01,"description":"Minimum fast/slow EMA separation normalized by ATR."},
        "support_tolerance_atr": {"label":"Support tolerance","unit":"ATR","min":0.05,"max":3,"step":0.05,"description":"Allowed distance from the structural support/resistance zone."},
        "atr_ratio_min": {"label":"Minimum volatility ratio","unit":"ratio","min":0.1,"max":3,"step":0.05,"description":"Lower bound for current ATR relative to its recent baseline."},
        "atr_ratio_max": {"label":"Maximum volatility ratio","unit":"ratio","min":0.2,"max":6,"step":0.05,"description":"Upper bound for current ATR relative to its recent baseline."},
        "min_impulse_atr": {"label":"Minimum impulse","unit":"ATR","min":0.1,"max":10,"step":0.05,"description":"Minimum directional impulse size normalized by ATR."},
        "min_retracement": {"label":"Minimum retracement","unit":"fraction","min":0.01,"max":0.8,"step":0.01,"description":"Minimum fraction of the impulse that price must retrace."},
        "max_retracement": {"label":"Maximum retracement","unit":"fraction","min":0.2,"max":0.99,"step":0.01,"description":"Maximum retracement before the original impulse setup is considered invalid."},
        "zone_tolerance_atr": {"label":"EMA value-zone tolerance","unit":"ATR","min":0.05,"max":3,"step":0.05,"description":"Distance around the EMA value zone accepted for a pullback."},
        "max_bars_since_pullback_low": {"label":"Maximum bars since pullback","unit":"bars","min":1,"max":100,"step":1,"description":"Maximum completed candles since the pullback pivot."},
        "rsi_overbought": {"label":"RSI overbought threshold","unit":"RSI","min":55,"max":95,"step":1,"description":"RSI extreme threshold; mirrored for short setups."},
        "breakout_lookback": {"label":"Breakout lookback","unit":"bars","min":5,"max":100,"step":1,"description":"Number of completed candles used to identify a breakout."},
        "breakout_buffer_atr": {"label":"Breakout buffer","unit":"ATR","min":0,"max":2,"step":0.01,"description":"Required close beyond the breakout level, normalized by ATR."},
        "breakout_retest_tolerance_atr": {"label":"Breakout retest tolerance","unit":"ATR","min":0.05,"max":3,"step":0.05,"description":"Maximum distance from the broken level accepted as a retest."},
        "breakout_max_retest_bars": {"label":"Maximum breakout retest age","unit":"bars","min":1,"max":30,"step":1,"description":"Maximum candles allowed for a breakout retest."},
    },
    "entry_timing": {
        "minimum_quality": {"label":"Minimum entry quality","unit":"0–1","min":0.05,"max":0.95,"step":0.01,"description":"Minimum computed entry quality required to enter."},
        "maximum_spread_points": {"label":"Maximum spread","unit":"points","min":1,"max":1000,"step":1,"description":"Absolute broker spread ceiling for entry timing."},
        "max_signal_age_bars": {"label":"Maximum signal age","unit":"bars","min":0,"max":10,"step":0.25,"description":"Maximum completed bars between signal and current evaluation."},
        "max_chase_atr": {"label":"Maximum price chase","unit":"ATR","min":0.05,"max":5,"step":0.05,"description":"Maximum price extension beyond the signal close."},
        "min_risk_room_atr": {"label":"Minimum room to invalidation","unit":"ATR","min":0.01,"max":3,"step":0.01,"description":"Minimum remaining distance to the structural invalidation level."},
        "max_spread_atr": {"label":"Maximum spread / ATR","unit":"ratio","min":0.01,"max":2,"step":0.01,"description":"Maximum spread as a fraction of ATR."},
        "max_spread_to_risk": {"label":"Maximum spread / risk","unit":"ratio","min":0.01,"max":2,"step":0.01,"description":"Maximum spread as a fraction of distance to invalidation."},
    },
    "risk_management": {
        "stop_buffer_atr": {"label":"Structural stop buffer","unit":"ATR","min":0,"max":2,"step":0.01,"description":"Additional stop distance beyond the structural invalidation level."},
        "min_stop_atr": {"label":"Minimum stop distance","unit":"ATR","min":0.1,"max":5,"step":0.05,"description":"Reject stops that are too close to market noise."},
        "max_stop_atr": {"label":"Maximum stop distance","unit":"ATR","min":0.5,"max":10,"step":0.05,"description":"Reject setups whose structural stop is too far away."},
        "maximum_risk_percent": {"label":"Maximum risk per trade","unit":"% of balance","min":0.1,"max":2.0,"step":0.05,"description":"Demo risk budget and hard per-trade ceiling. Position size is always rounded down; the broker minimum is never forced."},
        "max_total_risk_percent": {"label":"Maximum total portfolio risk","unit":"% of balance","min":0.25,"max":5.0,"step":0.05,"description":"Maximum combined open-position risk plus the proposed trade."},
        "max_daily_loss_percent": {"label":"Maximum daily loss","unit":"% of opening balance","min":0.5,"max":5.0,"step":0.1,"description":"Django blocks new entries when server-measured daily equity drawdown reaches this limit."},
        "target_reward_risk": {"label":"Target reward / risk","unit":"R:R","min":1.2,"max":5.0,"step":0.05,"description":"Take-profit distance as a multiple of the structural stop distance."},
        "min_reward_risk": {"label":"Minimum reward / risk","unit":"R:R","min":1.2,"max":3.0,"step":0.1,"description":"Reject trades whose planned target is below this reward/risk ratio. Cannot be lower than 1.2R."}
    },
}
DEMO_DEFAULTS = {
    "opportunities": {"minimum_score":0.42,"adx_trending":10.0,"adx_ranging":8.0,"min_ema_separation_atr":0.06,"regime_lookback":8,"support_tolerance_atr":1.0,"atr_ratio_min":0.6,"atr_ratio_max":2.5,"min_impulse_atr":0.8,"min_retracement":0.05,"max_retracement":0.95,"zone_tolerance_atr":0.9,"max_bars_since_pullback_low":16,"rsi_overbought":85.0,"breakout_lookback":12,"breakout_buffer_atr":0.0,"breakout_retest_tolerance_atr":0.5,"breakout_max_retest_bars":6},
    "entry_timing": {"minimum_quality":0.42,"maximum_spread_points":100.0,"max_signal_age_bars":1.5,"max_chase_atr":0.85,"min_risk_room_atr":0.15,"max_spread_atr":0.30,"max_spread_to_risk":0.40},
    "risk_management": {"maximum_risk_percent":1.6,"max_total_risk_percent":2.0,"max_daily_loss_percent":5.0,"stop_buffer_atr":0.2,"min_stop_atr":0.6,"max_stop_atr":3.0,"target_reward_risk":1.25,"min_reward_risk":1.2},
}
STANDARD_DEFAULTS = {
    "opportunities": {"minimum_score":0.55,"adx_trending":12.0,"adx_ranging":10.0,"min_ema_separation_atr":0.20,"regime_lookback":8,"support_tolerance_atr":0.65,"atr_ratio_min":0.6,"atr_ratio_max":2.5,"min_impulse_atr":1.5,"min_retracement":0.20,"max_retracement":0.82,"zone_tolerance_atr":0.40,"max_bars_since_pullback_low":8,"rsi_overbought":75.0,"breakout_lookback":20,"breakout_buffer_atr":0.10,"breakout_retest_tolerance_atr":0.25,"breakout_max_retest_bars":3},
    "entry_timing": {"minimum_quality":0.55,"maximum_spread_points":80.0,"max_signal_age_bars":1.0,"max_chase_atr":0.5,"min_risk_room_atr":0.25,"max_spread_atr":0.20,"max_spread_to_risk":0.25},
    "risk_management": {"maximum_risk_percent":0.5,"max_total_risk_percent":2.0,"max_daily_loss_percent":3.0,"stop_buffer_atr":0.25,"min_stop_atr":0.5,"max_stop_atr":3.5,"target_reward_risk":1.25,"min_reward_risk":1.2},
}

def defaults_for(profile):
    return deepcopy(DEMO_DEFAULTS if profile == "demo" else STANDARD_DEFAULTS)

def validate_values(values, profile):
    if not isinstance(values, dict):
        raise ValueError("values must be an object")
    result = defaults_for(profile)
    for group, updates in values.items():
        if group not in THRESHOLDS or not isinstance(updates, dict):
            raise ValueError(f"unknown threshold group: {group}")
        for key, raw in updates.items():
            spec = THRESHOLDS[group].get(key)
            if spec is None:
                raise ValueError(f"threshold is not editable: {group}.{key}")
            if isinstance(raw, bool):
                raise ValueError(f"{group}.{key} must be numeric")
            try:
                value = float(raw)
            except (TypeError, ValueError):
                raise ValueError(f"{group}.{key} must be numeric") from None
            if not (spec["min"] <= value <= spec["max"]):
                raise ValueError(f"{group}.{key} must be between {spec['min']} and {spec['max']}")
            if spec["step"] == 1 and not value.is_integer():
                raise ValueError(f"{group}.{key} must be a whole number")
            result[group][key] = int(value) if spec["step"] == 1 else value
    if result["opportunities"]["adx_ranging"] > result["opportunities"]["adx_trending"]:
        raise ValueError("ADX ranging threshold cannot exceed ADX trending threshold")
    if result["opportunities"]["min_retracement"] >= result["opportunities"]["max_retracement"]:
        raise ValueError("Minimum retracement must be below maximum retracement")
    if result["opportunities"]["atr_ratio_min"] >= result["opportunities"]["atr_ratio_max"]:
        raise ValueError("Minimum volatility ratio must be below maximum volatility ratio")
    risk = result["risk_management"]
    if risk["min_stop_atr"] >= risk["max_stop_atr"]:
        raise ValueError("Minimum stop distance must be below maximum stop distance")
    if risk["maximum_risk_percent"] > risk["max_total_risk_percent"]:
        raise ValueError("Maximum risk per trade cannot exceed maximum total portfolio risk")
    if risk["target_reward_risk"] < risk["min_reward_risk"]:
        raise ValueError("Target reward/risk must be greater than or equal to minimum reward/risk")
    return result

def public_schema(profile, saved=None):
    values = defaults_for(profile)
    if isinstance(saved, dict):
        for group, updates in saved.items():
            if group in values and isinstance(updates, dict):
                values[group].update({k:v for k,v in updates.items() if k in THRESHOLDS[group]})
    return {"profile":profile,"groups":[{"key":group,"title":group.replace("_"," ").title(),"items":[{"key":key,**spec,"value":values[group][key]} for key,spec in items.items()]} for group,items in THRESHOLDS.items()],"values":values}
