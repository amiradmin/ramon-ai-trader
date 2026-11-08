"""Readable orchestration for SignalFlow's four trading stages.

Stage order: opportunities -> entry timing -> risk management, with learning observing
outcomes alongside. This module evaluates a snapshot only. It does not submit orders or
persist outcomes; ``execution_enabled`` is always False.

Each stage receives the previous stages' results where that helps (entry timing sees the
opportunity, risk management sees the opportunity and the timing verdict) but no stage
reaches back to change an earlier decision.
"""
import inspect
from typing import Any, Mapping

from .common import DEFERRED
from .entry_timing import EntryTimer
from .learning import Learning
from .opportunities import OpportunityDetector
from .risk_management import RiskManager

# Server-owned calculation definitions. These describe the rules implemented by the
# corresponding stage methods; per-decision operands and active thresholds are attached
# from the actual stage result/config below. The dashboard must never supply fallbacks.
_CALCULATION_RULES = {
    "opportunities": {
        "symbol": "Pass when the symbol starts with one of OpportunityConfig.symbol_prefixes (unless the prefix filter is disabled).",
        "data_valid": "Validate completed OHLC bars, chronological timestamps, finite prices, candle ranges, required history, and configured recent-gap limits; indicator validity also requires finite ATR, ADX and RSI with ATR > 0.",
        "bar_complete": "Pass only when last_bar_closed is not False and, when require_closed_flag is enabled, last_bar_closed is exactly True.",
        "regime": "Compute EMA separation = (EMA_fast − EMA_slow) / ATR and slow-EMA slope = (EMA_slow[t] − EMA_slow[t − slope_lookback]) / ATR. Derive EMA bias using min_ema_separation_atr, support_tolerance_atr and slope sign; derive confirmed swing structure using pivot_window. Trending = ADX >= adx_trending. Classify BULLISH/BEARISH only when bias and structure are compatible and trending; classify RANGING when ADX < adx_ranging or the neutral/mixed/unknown fallback applies; otherwise UNCERTAIN.",
        "regime_stability": "Pass unless the regime regime_lookback bars ago is opposite to the current BULLISH/BEARISH regime.",
        "volatility": "ATR ratio = current ATR / median of finite ATR values in the last atr_baseline_bars. Pass when atr_ratio_min <= ratio <= atr_ratio_max.",
        "impulse": "Impulse size (ATR) = (oriented confirmed swing-high price − oriented confirmed swing-low price) / current ATR. The latest swing high must no longer be extending and the ratio must be >= min_impulse_atr.",
        "breakout_retest": "Alternative setup: a completed candle closes beyond the prior breakout_lookback-bar range by breakout_buffer_atr × ATR; a later completed candle retests the level within breakout_retest_tolerance_atr × ATR and closes on the breakout side; the current completed candle must close beyond the retest candle high (BUY) or low (SELL), within breakout_max_retest_bars. All levels exclude the breakout candle and use only candles available at the decision time.",
        "pullback_depth": "Retracement fraction = (oriented impulse high − oriented pullback low) / impulse size. Pass when min_retracement <= fraction <= max_retracement and the pullback does not break the impulse origin.",
        "trend_support": "For every completed candle from the impulse high through the current bar, require oriented close >= oriented slow EMA − support_tolerance_atr × current ATR.",
        "value_zone": "Value-zone gap (ATR) = max(0, oriented pullback low − oriented fast EMA at the pullback extreme) / current ATR. Pass when gap <= zone_tolerance_atr.",
        "freshness": "Bars since pullback extreme = current bar index − pullback-extreme index. Pass when this count <= max_bars_since_pullback_low.",
        "resumption": "Require at least one completed bar after the pullback extreme, with oriented current close > oriented fast EMA and oriented current close > previous oriented close.",
        "momentum": "Directional RSI = RSI for BUY, or 100 − RSI for SELL. Pass when directional RSI <= rsi_overbought.",
        "score": "Weighted opportunity score = sum(component_weight[name] × component_score[name]) for trend, strength, pullback, momentum and volatility. Pass when score >= minimum_score.",
        "duplicate": "Pass when no earlier accepted signal exists for the same symbol, timeframe, side and impulse-anchor timestamp.",
        "forecast": "Optional forecast veto: pass only when the enabled ARIMA/SARIMA evidence accepts the already-approved technical direction; forecasts cannot create an opportunity.",
        "failed_breakout_sweep": "A completed candle trades beyond a prior lookback high/low by sweep_buffer_atr × ATR and closes back inside that prior range; the sweep extreme is the invalidation reference.",
        "range_edge_fade": "Only in the RANGING regime: require a prior two-sided range within configured ATR width bounds, repeated reactions at both boundaries, a wick rejection, material close back inside the range, and a directional close-location threshold; Django targets the range midpoint only when it meets the minimum reward/risk, otherwise it rejects the trade.",
        "session_opening_range_breakout": "After the configured IANA-timezone opening range has fully completed, require a completed close beyond its high/low by breakout_buffer_atr × ATR. Session clock uses timezone-aware local time and DST.",
        "volatility_compression_expansion": "Require the preceding compression_lookback_bars range to fit within compression_max_atr × current ATR × lookback, followed by a directional candle body >= expansion_body_atr × ATR.",
        "exhaustion_reversal": "Last-priority setup: require an extension of minimum_extension_atr over extension_lookback_bars, RSI extreme, and a close through the last confirmed opposite swing (CHoCH).",
        "configuration": "Stage thresholds are loaded from the versioned trading_config.json. Structural opportunity results expose config_version for audit; invalid configuration fails closed."
    },
    "entry_timing": {
        "freshness": "Signal age (bars) = (latest completed bar time − signal bar time) / timeframe seconds. Reject future signal times and pass only when age <= max_signal_age_bars.",
        "invalidation": "Room to invalidation (ATR) = (entry quote − invalidation price) × direction sign / setup ATR. Price must remain on the valid side and room must be >= min_risk_room_atr.",
        "chase": "Chase distance (ATR) = (entry quote − signal reference price) × direction sign / setup ATR. Pass when distance <= max_chase_atr.",
        "spread_absolute": "Absolute spread (points) = (ask − bid) / point (or the supplied spread_points when no quote is available). Pass when 0 <= spread <= maximum_spread_points.",
        "spread_volatility": "Spread fraction of ATR = ((ask − bid) / point × point) / setup ATR. Pass when fraction <= max_spread_atr.",
        "spread_risk": "Spread-to-risk = spread price / directional distance from entry quote to invalidation. Pass when ratio <= max_spread_to_risk.",
        "price_checks": "When a complete bid, ask and point quote is unavailable, price-dependent invalidation, chase and relative-spread checks are skipped (or rejected when require_quote is enabled).",
        "quote_required": "Require valid positive bid, ask and point values before quote-dependent timing checks can run.",
        "invalid_opportunity_setup": "Require an approved BUY/SELL setup with finite positive ATR, reference price and invalidation price; invalidation must be on the protective side of the reference price.",
        "signal_time_unavailable": "When require_signal_time is enabled, both signal time and latest bar time must be present to calculate signal age.",
        "signal_time_invalid": "Signal age must be non-negative: (latest completed bar time − signal bar time) / timeframe seconds >= 0.",
        "signal_expired": "Signal age in bars must be <= max_signal_age_bars.",
        "quality": "If supplied quality is accepted, use market.entry_quality. Otherwise quality = 0.40 × spread_eff + 0.35 × location + 0.25 × freshness, where spread_eff = 1 − 0.5 × clip(spread_ATR / max_spread_atr), location = 1 − 0.5 × clip(max(chase_ATR, 0) / max_chase_atr), and freshness = 1 − age_bars / (max_signal_age_bars + 1). Pass when 0 <= quality <= 1 and quality >= minimum_quality.",
    },
    "risk_management": {
        "stop": "Structural stop = invalidation price − direction_sign × stop_buffer_atr × setup ATR (SELL stop may additionally be moved by ask − bid when short_stop_spread_adjust is enabled). Stop distance = (entry price − stop price) × direction_sign; normalize by ATR and require min_stop_atr <= stop_ATR <= max_stop_atr. Legacy mode uses supplied stop_loss_points.",
        "stop_below_broker_minimum": "Pass when planned stop distance in points >= broker stop_level_points.",
        "reward_risk": "When target_reward_risk is supplied, planned take-profit distance = structural stop distance × target_reward_risk; require target_reward_risk >= min_reward_risk. Legacy callers may still supply take_profit_points directly.",
        "daily_loss": "When server-derived daily_loss_percent is supplied, pass only when daily_loss_percent < max_daily_loss_percent; otherwise this guard is explicitly skipped.",
        "portfolio_risk": "Available headroom = max_total_risk_percent − open_risk_percent. When open_risk_percent is supplied, require headroom > 0 and cap this trade's risk percent to headroom; otherwise this guard is explicitly skipped.",
        "sizing": "Effective risk percent = min(requested risk percent, maximum_risk_percent, available portfolio headroom when supplied). Risk budget = account balance × effective risk percent / 100. Raw lots = risk budget / (stop points × point value per lot); cap at max_volume, round down to volume_step, and reject if below min_volume. Estimated risk = rounded lots × stop points × point value per lot and must not exceed budget.",
        "missing_or_invalid_risk_input": "All required account, volume, stop and symbol inputs must be present and numeric.",
        "non_finite_risk_input": "All required account, volume, stop and symbol inputs must be finite numbers.",
        "non_positive_or_inconsistent_risk_input": "Balance, requested risk percent, stop distance, point value, volume step and minimum volume must be positive; maximum volume must be >= minimum volume.",
        "entry_beyond_stop": "Directional distance from selected entry price to calculated stop price must be > 0.",
        "stop_too_tight": "Structural stop distance normalized by ATR must be >= min_stop_atr.",
        "stop_too_wide": "Structural stop distance normalized by ATR must be <= max_stop_atr.",
        "stop_below_broker_minimum": "Planned stop distance in points must be >= broker stop_level_points.",
        "reward_risk_too_low": "The configured target_reward_risk must be >= min_reward_risk; the executable take-profit is derived from the same structural stop distance and target multiple.",
        "daily_loss_limit_reached": "Server-derived daily_loss_percent must be < max_daily_loss_percent.",
        "portfolio_risk_limit_reached": "max_total_risk_percent − open_risk_percent must be > 0.",
        "risk_budget_below_minimum_volume": "After capping at max_volume and rounding down to volume_step, calculated volume must be >= min_volume; never round up to the broker minimum.",
        "risk_budget_exceeded": "Estimated risk from rounded volume must be <= calculated risk budget.",
        "upstream_stage_rejected": "Risk sizing is not evaluated unless both Opportunities and Entry Timing have approved.",
        "invalid_opportunity_setup": "Require an approved BUY/SELL opportunity with finite positive ATR, invalidation price, reference price and symbol point; invalidation must be on the protective side of the reference price.",
    },
}

def _complete_stage_checks(stages: dict[str, Any]) -> None:
    """Keep every documented gate visible, even when a prerequisite was not reached.

    Missing checks are marked NOT_EVALUATED. This audit metadata never changes
    approval, status, sizing, or execution authorization.
    """
    for stage_name, rules in _CALCULATION_RULES.items():
        stage = stages.get(stage_name)
        if not isinstance(stage, dict):
            continue
        checks = stage.setdefault("checks", [])
        if not isinstance(checks, list):
            continue
        existing = {
            str(item.get("name", "")).lower()
            for item in checks if isinstance(item, dict)
        }
        for name in rules:
            if name not in existing:
                checks.append({
                    "name": name,
                    "passed": None,
                    "status": "NOT_EVALUATED",
                    "detail": "Not evaluated in this decision snapshot; an earlier gate or prerequisite prevented evaluation.",
                })

def _plain_config(config: Any) -> dict[str, Any]:
    if config is None:
        return {}
    values = vars(config) if hasattr(config, "__dict__") else {}
    return {key: list(value) if isinstance(value, tuple) else value for key, value in values.items()}

def _attach_calculation_metadata(
    stages: dict[str, Any],
    configs: Mapping[str, Mapping[str, Any]],
    market: Mapping[str, Any],
    opportunity: Mapping[str, Any],
    timing: Mapping[str, Any],
) -> None:
    """Attach server-authored rules plus actual per-decision inputs; never changes verdicts."""

    for stage_name, stage in stages.items():
        if not isinstance(stage, dict):
            continue
        checks = stage.get("checks")
        if not isinstance(checks, list):
            continue
        stage_inputs = {}
        for key in ("regime", "components", "setup", "score", "side", "signal_bar_time",
                    "signal_age_bars", "target_reward_risk", "take_profit_points", "max_total_risk_percent", "max_daily_loss_percent", "room_to_invalidation_atr", "chase_atr", "spread_points",
                    "spread_atr_fraction", "spread_to_risk", "quality", "quality_source",
                    "quote_price", "entry_price", "stop_price", "stop_points", "stop_atr",
                    "stop_source", "take_profit_points", "reward_risk", "risk_percent",
                    "risk_budget", "estimated_risk", "volume", "risk_capped"):
            if key in stage:
                stage_inputs[key] = stage[key]
        for check in checks:
            if not isinstance(check, dict):
                continue
            name = str(check.get("name", "")).lower()
            calculation = {
                "formula": _CALCULATION_RULES.get(stage_name, {}).get(name),
                "inputs": {
                    "observed_check_metrics": check.get("metrics", {}),
                    "stage_values": stage_inputs,
                    "configured_thresholds": stage.get("thresholds", {}) or configs.get(stage_name, {}),
                    "upstream_opportunity_setup": opportunity.get("setup", {}),
                    "upstream_entry_timing": {
                        key: timing[key]
                        for key in ("quote_price", "entry_price", "side", "signal_age_bars",
                                    "room_to_invalidation_atr", "chase_atr", "spread_points",
                                    "spread_atr_fraction", "spread_to_risk", "quality")
                        if key in timing
                    },
                    "market_inputs": {
                        key: market[key]
                        for key in (
                            "symbol", "timeframe", "bid", "ask", "point", "spread_points", "config_version",
                            "entry_quality", "account_balance", "risk_percent", "stop_loss_points",
                            "point_value_per_lot", "volume_step", "min_volume", "max_volume",
                            "stop_level_points", "take_profit_points", "target_reward_risk", "open_risk_percent",
                            "daily_loss_percent",
                        )
                        if key in market
                    },
                },
                "result": "PASS" if check.get("passed") is True else "FAIL" if check.get("passed") is False else "NOT_EVALUATED",
                "source": "django_signalflow",
            }
            check["calculation"] = calculation


CONTRACT_VERSION = "2"
_STAGE_ORDER = ("opportunities", "entry_timing", "risk_management")


class SignalFlowPipeline:
    def __init__(self, opportunities: OpportunityDetector | None = None, entry_timer: EntryTimer | None = None, risk_manager: RiskManager | None = None, learning: Learning | None = None) -> None:
        self.opportunities = opportunities or OpportunityDetector()
        self.entry_timer = entry_timer or EntryTimer()
        self.risk_manager = risk_manager or RiskManager()
        self.learning = learning or Learning()

    def _evaluate_risk(self, market: Mapping[str, Any], upstream: bool, opportunity: Mapping[str, Any], timing: Mapping[str, Any]) -> dict[str, Any]:
        """Call the risk stage with stage context, tolerating custom managers with the old signature."""
        parameters = inspect.signature(self.risk_manager.evaluate).parameters
        accepts_context = "opportunity" in parameters or any(p.kind is inspect.Parameter.VAR_KEYWORD for p in parameters.values())
        if accepts_context:
            return self.risk_manager.evaluate(market, upstream, opportunity=opportunity, entry=timing)
        return self.risk_manager.evaluate(market, upstream)

    def run(self, market: Mapping[str, Any], outcome: Mapping[str, Any] | None = None) -> dict[str, Any]:
        learning_result = self.learning.observe(outcome)
        opportunity = self.opportunities.evaluate(market)
        timing = self.entry_timer.evaluate(opportunity, market)
        risk = self._evaluate_risk(market, bool(opportunity.get("approved") and timing.get("approved")), opportunity, timing)
        stages = {"opportunities": opportunity, "entry_timing": timing, "risk_management": risk}
        # Keep every documented gate visible; missing gates remain NOT_EVALUATED.
        _complete_stage_checks(stages)
        # Attach auditable calculation metadata after all verdicts are complete.
        # Metadata is descriptive only and cannot alter any stage approval.
        _attach_calculation_metadata(stages, {
            "opportunities": {**_plain_config(getattr(self.opportunities, "config", None)),
                              "minimum_score": getattr(self.opportunities, "minimum_score", None)},
            "entry_timing": {**_plain_config(getattr(self.entry_timer, "config", None)),
                             "minimum_quality": getattr(self.entry_timer, "minimum_quality", None),
                             "maximum_spread_points": getattr(self.entry_timer, "maximum_spread_points", None),
                             "require_quote": getattr(self.entry_timer, "require_quote", None)},
            "risk_management": {**_plain_config(getattr(self.risk_manager, "config", None)),
                                "maximum_risk_percent": getattr(self.risk_manager, "maximum_risk_percent", None)},
        }, market, opportunity, timing)
        approved = all(bool(stages[name].get("approved")) for name in _STAGE_ORDER)
        action = opportunity.get("side", "HOLD") if approved else "HOLD"
        rejected_by = next((name for name in _STAGE_ORDER if not stages[name].get("approved")), None)
        # A later stage that DEFERS means "not now, try again". Give the signal back to the
        # detector so the next bar is not reported as DUPLICATE_SIGNAL.
        if opportunity.get("approved") and not approved:
            deferred = any(stages[name].get("status") == DEFERRED for name in _STAGE_ORDER[1:])
            release = getattr(self.opportunities, "release", None)
            if deferred and callable(release) and opportunity.get("dedupe_key"):
                release(opportunity["dedupe_key"], opportunity.get("signal_bar_time"))
        return {
            "system": "SignalFlow",
            "contract_version": CONTRACT_VERSION,
            "action": action,
            "approved": approved,
            "rejected_by": rejected_by,
            "reason": "SIGNALFLOW_APPROVED" if approved else stages[rejected_by].get("reason", "STAGE_REJECTED"),
            "stages": {**stages, "learning": learning_result},
            "execution_enabled": False,
        }
