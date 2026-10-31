"""Stage 3: place the protective stop and size a trade within a conservative risk budget.

Stages 1-2 decide *what* and *when*; this stage only decides *how much*, and it can
only ever shrink or refuse a trade, never change its direction or approve something
upstream rejected.

Two ways to obtain the stop distance:

* **Structural** - when the approved ``opportunity`` (and optionally the ``entry``
  timing result) are passed in, the stop is the setup's invalidation level pushed
  ``stop_buffer_atr`` ATR beyond it, measured from the entry quote. Sanity bounds
  against ATR and the broker's minimum stop distance apply.
* **Legacy** - ``market["stop_loss_points"]`` supplied by the caller (what the Django
  endpoint passes today). Behaviour and reason codes are unchanged. It is also the
  fallback when an opportunity is supplied but the market has no ``point``.

Sizing is the original rule: risk budget = balance * min(requested %, maximum %),
volume = budget / (stop points * point value), rounded *down* to the volume step and
never rounded up to the broker minimum. Optional account-state inputs add guards:
``open_risk_percent`` (portfolio cap), ``daily_loss_percent`` (daily loss halt) and
``take_profit_points`` (minimum reward:risk). Guards whose input is absent are reported
as skipped, not silently assumed satisfied.

Results always contain ``approved``, ``reason`` and ``volume`` (what ``pipeline.py``
reads) plus ``status`` (``ACCEPTED`` / ``REJECTED`` / ``DEFERRED``), ``checks`` and an
``explanation``. This module never places orders.
"""
import math
from dataclasses import dataclass
from typing import Any, Mapping

from .common import ACCEPTED, DEFERRED, REJECTED

_LEGACY_REQUIRED = (
    "account_balance", "risk_percent", "stop_loss_points", "point_value_per_lot",
    "volume_step", "min_volume", "max_volume",
)


@dataclass(frozen=True)
class RiskConfig:
    """Thresholds beyond the per-trade percentage. Conventional priors, not optimised values."""

    stop_buffer_atr: float = 0.25  # distance beyond the invalidation level
    min_stop_atr: float = 0.5  # tighter stops are noise-level
    max_stop_atr: float = 3.0  # wider stops mean the structure is a poor place to risk
    min_reward_risk: float = 1.5  # applied only when take_profit_points is supplied
    max_total_risk_percent: float = 2.0  # open risk + this trade, applied when open_risk_percent is supplied
    max_daily_loss_percent: float = 3.0  # halt after this realised loss, applied when daily_loss_percent is supplied
    # Validation / safety options. Defaults keep the original behaviour.
    # strict_structural: with a structural opportunity but no ``point``, reject instead of silently
    # falling back to the caller-supplied stop_loss_points.
    strict_structural: bool = False
    # short_stop_spread_adjust: a SELL stop triggers on the ask while structure is read on the bid
    # chart, so move a short stop up by the current spread to keep it on the same structural level.
    short_stop_spread_adjust: bool = False

    def __post_init__(self) -> None:
        if self.stop_buffer_atr < 0:
            raise ValueError("stop_buffer_atr must not be negative")
        if not 0 < self.min_stop_atr < self.max_stop_atr:
            raise ValueError("stop ATR bounds must satisfy 0 < min < max")
        for name in ("min_reward_risk", "max_total_risk_percent", "max_daily_loss_percent"):
            if not getattr(self, name) > 0:
                raise ValueError(f"{name} must be positive")


class _Reject(Exception):
    def __init__(self, reason: str, detail: str, status: str = REJECTED, **fields: Any) -> None:
        super().__init__(detail)
        self.reason, self.detail, self.status, self.fields = reason, detail, status, fields


def _optional(market: Mapping[str, Any], key: str) -> float | None:
    """Parse an optional non-negative input; absent/None means 'not supplied', garbage is rejected."""
    value = market.get(key)
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise _Reject("MISSING_OR_INVALID_RISK_INPUT", f"{key} must be numeric") from None
    if not math.isfinite(number):
        raise _Reject("NON_FINITE_RISK_INPUT", f"{key} must be finite")
    if number < 0:
        raise _Reject("NON_POSITIVE_OR_INCONSISTENT_RISK_INPUT", f"{key} must not be negative")
    return number


class RiskManager:
    def __init__(self, maximum_risk_percent: float = 0.5, *, config: RiskConfig | None = None) -> None:
        if not 0.0 < maximum_risk_percent <= 100.0:
            raise ValueError("maximum_risk_percent must be in (0, 100]")
        self.maximum_risk_percent = maximum_risk_percent
        self.config = config or RiskConfig()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def evaluate(
        self,
        market: Mapping[str, Any],
        upstream_approved: bool,
        *,
        opportunity: Mapping[str, Any] | None = None,
        entry: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not upstream_approved:
            return self._reject(REJECTED, "UPSTREAM_STAGE_REJECTED", "an earlier stage did not approve this trade", [])
        checks: list[dict[str, Any]] = []
        if not isinstance(market, Mapping):
            return self._reject(REJECTED, "MISSING_OR_INVALID_RISK_INPUT", "market snapshot must be a mapping", checks)
        try:
            return self._size(market, opportunity, entry, checks)
        except _Reject as exc:
            checks.append({"name": exc.reason.lower(), "passed": False, "detail": exc.detail})
            return self._reject(exc.status, exc.reason, exc.detail, checks, **exc.fields)

    @staticmethod
    def _reject(status: str, reason: str, detail: str, checks: list[dict[str, Any]], **fields: Any) -> dict[str, Any]:
        result = {
            "approved": False,
            "status": status,
            "reason": reason,
            "volume": 0.0,
            "explanation": f"trade not sized ({status.lower()}): {detail}",
            "checks": checks,
        }
        result.update(fields)
        return result

    # ------------------------------------------------------------------
    # Stop determination
    # ------------------------------------------------------------------

    def _structural_stop(
        self, market: Mapping[str, Any], opportunity: Mapping[str, Any], entry: Mapping[str, Any] | None
    ) -> dict[str, Any]:
        cfg = self.config
        setup = opportunity["setup"]
        side = str(opportunity.get("side", "")).upper()
        if side not in {"BUY", "SELL"}:
            raise _Reject("INVALID_OPPORTUNITY_SETUP", "approved opportunity has no BUY/SELL side")
        sign = 1 if side == "BUY" else -1
        try:
            atr, invalidation, reference = (float(setup[k]) for k in ("atr", "invalidation_price", "reference_price"))
            point = float(market["point"])
        except (KeyError, TypeError, ValueError):
            raise _Reject("MISSING_OR_INVALID_RISK_INPUT", "setup atr/invalidation_price/reference_price and market point are required") from None
        if not all(math.isfinite(x) and x > 0 for x in (atr, invalidation, reference, point)):
            raise _Reject("INVALID_OPPORTUNITY_SETUP", "setup prices, ATR and point must be positive and finite")
        if (reference - invalidation) * sign <= 0:
            raise _Reject("INVALID_OPPORTUNITY_SETUP", "invalidation level is on the wrong side of the reference price")

        entry_price, source = reference, "SIGNAL_REFERENCE"
        quoted = entry.get("quote_price") if isinstance(entry, Mapping) else None
        try:
            if quoted is not None and float(quoted) > 0 and math.isfinite(float(quoted)):
                entry_price, source = float(quoted), "TIMING_QUOTE"
            else:
                market_quote = market.get("ask" if sign > 0 else "bid")
                if market_quote is not None and math.isfinite(float(market_quote)) and float(market_quote) > 0:
                    entry_price, source = float(market_quote), "MARKET_QUOTE"
        except (TypeError, ValueError):
            raise _Reject("MISSING_OR_INVALID_RISK_INPUT", "entry quote must be numeric") from None

        stop_price = invalidation - sign * cfg.stop_buffer_atr * atr
        if cfg.short_stop_spread_adjust and sign < 0:
            try:
                bid_q, ask_q = float(market["bid"]), float(market["ask"])
            except (KeyError, TypeError, ValueError):
                raise _Reject("MISSING_OR_INVALID_RISK_INPUT", "bid and ask are required to adjust a short stop for the spread") from None
            if not (math.isfinite(bid_q) and math.isfinite(ask_q) and 0 < bid_q <= ask_q):
                raise _Reject("MISSING_OR_INVALID_RISK_INPUT", "bid and ask must be positive with ask >= bid")
            stop_price += ask_q - bid_q
        distance = (entry_price - stop_price) * sign
        if distance <= 0:
            raise _Reject("ENTRY_BEYOND_STOP", f"entry {entry_price:g} is already beyond the stop level {stop_price:g}")
        stop_atr = distance / atr
        if stop_atr < cfg.min_stop_atr:
            raise _Reject("STOP_TOO_TIGHT", f"stop is only {stop_atr:.2f} ATR away (minimum {cfg.min_stop_atr})", stop_atr=round(stop_atr, 3))
        if stop_atr > cfg.max_stop_atr:
            raise _Reject("STOP_TOO_WIDE", f"stop is {stop_atr:.2f} ATR away (maximum {cfg.max_stop_atr})", stop_atr=round(stop_atr, 3))
        return {
            "stop_points": distance / point,
            "stop_price": round(stop_price, 5),
            "stop_atr": round(stop_atr, 3),
            "stop_source": "STRUCTURAL",
            "entry_price_source": source,
            "side": side,
        }

    # ------------------------------------------------------------------
    # Sizing
    # ------------------------------------------------------------------

    def _size(
        self,
        market: Mapping[str, Any],
        opportunity: Mapping[str, Any] | None,
        entry: Mapping[str, Any] | None,
        checks: list[dict[str, Any]],
    ) -> dict[str, Any]:
        cfg = self.config
        structural = isinstance(opportunity, Mapping) and isinstance(opportunity.get("setup"), Mapping)
        fallback_note = None
        if (structural and not cfg.strict_structural and market.get("point") is None
                and market.get("stop_loss_points") is not None):
            # Callers that only supply a stop distance keep working: without ``point`` the
            # structural stop cannot be converted to points, so the supplied stop is used.
            structural = False
            fallback_note = "no point supplied, so the structural stop was not placed; using the supplied stop_loss_points"
        details: dict[str, Any] = {}
        if structural:
            if not opportunity.get("approved"):
                raise _Reject("UPSTREAM_STAGE_REJECTED", "the supplied opportunity is not approved")
            details = self._structural_stop(market, opportunity, entry)
            checks.append({"name": "stop", "passed": True, "detail": (
                f"structural stop {details['stop_price']:g}, {details['stop_atr']:.2f} ATR "
                f"({details['stop_points']:.0f} points) from the {details['entry_price_source'].lower()} entry")})
        required = tuple(k for k in _LEGACY_REQUIRED if not (structural and k == "stop_loss_points"))
        try:
            values = {key: float(market[key]) for key in required}
        except (KeyError, TypeError, ValueError):
            raise _Reject("MISSING_OR_INVALID_RISK_INPUT", "a required account, volume or stop input is missing or not numeric") from None
        if structural:
            values["stop_loss_points"] = details["stop_points"]
        else:
            details = {"stop_source": "SUPPLIED"}
        if not all(math.isfinite(v) for v in values.values()):
            raise _Reject("NON_FINITE_RISK_INPUT", "all risk inputs must be finite numbers")
        balance, requested_pct, stop_points, point_value, step, min_volume, max_volume = (
            values[k] for k in _LEGACY_REQUIRED
        )
        if (balance <= 0 or requested_pct <= 0 or stop_points <= 0 or point_value <= 0 or step <= 0
                or min_volume <= 0 or max_volume < min_volume):
            raise _Reject("NON_POSITIVE_OR_INCONSISTENT_RISK_INPUT", "balance, risk %, stop, point value and volumes must be positive and consistent")
        if not structural:
            checks.append({"name": "stop", "passed": True, "detail": fallback_note or f"supplied stop of {stop_points:g} points"})

        broker_min = _optional(market, "stop_level_points")
        if broker_min is not None and stop_points < broker_min:
            raise _Reject("STOP_BELOW_BROKER_MINIMUM", f"stop of {stop_points:.0f} points is inside the broker minimum of {broker_min:g}")

        reward_risk = None
        take_profit = _optional(market, "take_profit_points")
        if take_profit is None:
            checks.append({"name": "reward_risk", "passed": True, "detail": "skipped: no take_profit_points supplied"})
        else:
            reward_risk = take_profit / stop_points
            if reward_risk < cfg.min_reward_risk:
                raise _Reject("REWARD_RISK_TOO_LOW", f"reward:risk is {reward_risk:.2f} (minimum {cfg.min_reward_risk})", reward_risk=round(reward_risk, 3))
            checks.append({"name": "reward_risk", "passed": True, "detail": f"reward:risk {reward_risk:.2f}"})

        daily_loss = _optional(market, "daily_loss_percent")
        if daily_loss is None:
            checks.append({"name": "daily_loss", "passed": True, "detail": "skipped: no daily_loss_percent supplied"})
        else:
            if daily_loss >= cfg.max_daily_loss_percent:
                raise _Reject("DAILY_LOSS_LIMIT_REACHED", f"daily loss today is {daily_loss:g}% (limit {cfg.max_daily_loss_percent:g}%)")
            checks.append({"name": "daily_loss", "passed": True, "detail": f"daily loss {daily_loss:g}% below {cfg.max_daily_loss_percent:g}%"})

        risk_pct = min(requested_pct, self.maximum_risk_percent)
        open_risk = _optional(market, "open_risk_percent")
        if open_risk is None:
            checks.append({"name": "portfolio_risk", "passed": True, "detail": "skipped: no open_risk_percent supplied"})
        else:
            headroom = round(cfg.max_total_risk_percent - open_risk, 8)
            if headroom <= 0:
                raise _Reject("PORTFOLIO_RISK_LIMIT_REACHED", f"open risk {open_risk:g}% already uses the {cfg.max_total_risk_percent:g}% limit", status=DEFERRED)
            risk_pct = min(risk_pct, headroom)
            checks.append({"name": "portfolio_risk", "passed": True, "detail": f"{headroom:g}% of portfolio risk headroom"})

        risk_budget = balance * risk_pct / 100.0
        raw_volume = risk_budget / (stop_points * point_value)
        volume = math.floor((min(raw_volume, max_volume) + 1e-12) / step) * step
        volume = round(volume, 8)
        if volume < min_volume:
            raise _Reject("RISK_BUDGET_BELOW_MINIMUM_VOLUME",
                          f"budget of {risk_budget:g} buys {raw_volume:.4f} lots, below the {min_volume:g} lot minimum (not rounded up)",
                          risk_budget=risk_budget)
        estimated_risk = volume * stop_points * point_value
        if estimated_risk > risk_budget + 1e-9:
            raise _Reject("RISK_BUDGET_EXCEEDED", "rounded volume would risk more than the budget", risk_budget=risk_budget)
        checks.append({"name": "sizing", "passed": True, "detail": f"{volume:g} lots risks {estimated_risk:g} of a {risk_budget:g} budget"})

        result: dict[str, Any] = {
            "approved": True,
            "status": ACCEPTED,
            "reason": "RISK_APPROVED",
            "volume": volume,
            "risk_budget": round(risk_budget, 8),
            "estimated_risk": round(estimated_risk, 8),
            "risk_percent": risk_pct,
            "risk_capped": risk_pct < requested_pct,
            "stop_points": round(stop_points, 4),
            "reward_risk": None if reward_risk is None else round(reward_risk, 3),
            "checks": checks,
        }
        result.update({k: v for k, v in details.items() if k != "stop_points"})
        result["explanation"] = (
            f"{volume:g} lots approved: risking {estimated_risk:g} ({risk_pct:g}% budget {risk_budget:g}) "
            f"on a {stop_points:.0f} point {details['stop_source'].lower()} stop"
        )
        return result
