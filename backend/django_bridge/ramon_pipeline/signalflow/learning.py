"""Stage 4: validate trade outcomes and turn them into leak-safe, advisory performance evidence.

``Learning`` never changes how the other stages behave. It has no write path into the
opportunity, entry-timing or risk thresholds: it validates outcomes, optionally records
them in an injected store, and reports statistics and findings that a human (or a
separately validated process) may act on. Nothing here is applied automatically.

Behaviour is layered so the original contract keeps working:

* ``Learning()`` with no store validates only and reports
  ``OUTCOME_VALIDATED_NOT_PERSISTED`` exactly as before.
* ``Learning(store=...)`` additionally records valid outcomes (``OUTCOME_RECORDED``),
  ignores exact re-deliveries (``DUPLICATE_OUTCOME_IGNORED``) and refuses a different
  outcome for an already-recorded trade (``CONFLICTING_OUTCOME``) instead of overwriting.
  ``InMemoryOutcomeStore`` is provided; durable storage belongs to the caller and only
  has to implement ``add`` and ``all``.

Statistics are built to resist the usual self-deception:

* ``as_of`` restricts every statistic to trades that had *closed* by that time, so
  evidence used in a replay or backtest cannot contain the future. Records without a
  close time are excluded (and counted) when ``as_of`` is given.
* Findings need a minimum sample, use confidence bounds rather than point estimates, use a
  stricter bound for sub-segment comparisons (many comparisons produce false positives)
  and are marked ``advisory`` with ``auto_apply: False``.
* ``realized_pnl`` is taken as given and should already be net of spread, commission and
  swap; learning cannot verify that.
"""
import math
import threading
from collections import defaultdict
from typing import Any, Iterable, Mapping, Protocol

from .common import InvalidMarketData, parse_timestamp

_Z_OVERALL = 1.96  # ~95% two-sided
_Z_SEGMENT = 2.58  # ~99%: stricter because many segments are compared
_ALLOWED_SIDES = {"BUY", "SELL"}
_TAG_KEYS = ("side", "regime", "exit_reason", "setup_type", "forecast_confirmed", "signal_id", "score")
_SEGMENT_KEYS = ("side", "regime", "exit_reason")


class OutcomeStore(Protocol):
    def add(self, record: Mapping[str, Any]) -> bool:
        """Store a record; return False (and store nothing) if the trade_id already exists."""

    def get(self, trade_id: str) -> Mapping[str, Any] | None: ...

    def all(self) -> list[Mapping[str, Any]]: ...


class InMemoryOutcomeStore:
    """Thread-safe, process-local store. Records are copied in and out, so callers cannot mutate history."""

    def __init__(self) -> None:
        self._records: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def add(self, record: Mapping[str, Any]) -> bool:
        with self._lock:
            if record["trade_id"] in self._records:
                return False
            self._records[record["trade_id"]] = dict(record)
            return True

    def get(self, trade_id: str) -> Mapping[str, Any] | None:
        with self._lock:
            found = self._records.get(trade_id)
            return dict(found) if found is not None else None

    def all(self) -> list[Mapping[str, Any]]:
        with self._lock:
            return [dict(r) for r in self._records.values()]


class _Invalid(ValueError):
    pass


def _finite(value: Any, name: str) -> float:
    if isinstance(value, bool):
        raise _Invalid(f"{name} must be numeric")
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise _Invalid(f"{name} must be numeric") from None
    if not math.isfinite(number):
        raise _Invalid(f"{name} must be finite")
    return number


def _wilson(wins: int, n: int, z: float = _Z_OVERALL) -> tuple[float, float]:
    if n == 0:
        return 0.0, 1.0
    p = wins / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, centre - margin), min(1.0, centre + margin)


def _mean_bounds(values: list[float], z: float) -> tuple[float, float, float] | None:
    """(mean, lower, upper) of the mean using a normal approximation; None if fewer than 2 values."""
    n = len(values)
    if n < 2:
        return None
    mean = sum(values) / n
    variance = sum((v - mean) ** 2 for v in values) / (n - 1)
    se = math.sqrt(variance / n)
    return mean, mean - z * se, mean + z * se


class Learning:
    def __init__(self, store: OutcomeStore | None = None) -> None:
        self.store = store

    # ------------------------------------------------------------------
    # Context helper for the executor
    # ------------------------------------------------------------------

    @staticmethod
    def build_outcome_context(stages: Mapping[str, Any]) -> dict[str, Any]:
        """Extract the fields worth keeping with a trade from a pipeline result's ``stages``.

        Store this with the order when it is placed and merge it into the outcome when the
        trade closes, so outcomes can be attributed to the signal that produced them.
        """
        opportunity = stages.get("opportunities") or {}
        risk = stages.get("risk_management") or {}
        regime = opportunity.get("regime") or {}
        setup = opportunity.get("setup") or {}
        context: dict[str, Any] = {
            "signal_id": opportunity.get("signal_id"),
            "side": opportunity.get("side") if opportunity.get("approved") else None,
            "regime": regime.get("label"),
            "setup_type": setup.get("type"),
            "score": opportunity.get("score"),
            "forecast_confirmed": bool(
                opportunity.get("reason") == "OPPORTUNITY_FOUND_WITH_FORECAST_CONFIRMATION"
            ),
            "risk_amount": risk.get("estimated_risk"),
        }
        return {k: v for k, v in context.items() if v is not None}

    # ------------------------------------------------------------------
    # Observing outcomes
    # ------------------------------------------------------------------

    def observe(self, outcome: Mapping[str, Any] | None) -> dict[str, Any]:
        if outcome is None:
            return {"accepted": False, "reason": "NO_OUTCOME_PROVIDED"}
        if not isinstance(outcome, Mapping):
            return {"accepted": False, "reason": "INVALID_OUTCOME", "detail": "outcome must be a mapping"}
        trade_id = str(outcome.get("trade_id", "")).strip()
        try:
            pnl = _finite(outcome["realized_pnl"], "realized_pnl")
        except KeyError:
            return {"accepted": False, "reason": "INVALID_OUTCOME", "detail": "realized_pnl is required"}
        except _Invalid as exc:
            return {"accepted": False, "reason": "INVALID_OUTCOME", "detail": str(exc)}
        if not trade_id:
            return {"accepted": False, "reason": "MISSING_TRADE_ID"}
        try:
            record = self._build_record(outcome, trade_id, pnl)
        except (_Invalid, InvalidMarketData) as exc:
            detail = exc.detail if isinstance(exc, InvalidMarketData) else str(exc)
            return {"accepted": False, "reason": "INVALID_OUTCOME", "detail": detail}

        result = "WIN" if pnl > 0 else "LOSS" if pnl < 0 else "BREAKEVEN"
        response: dict[str, Any] = {
            "accepted": True,
            "trade_id": trade_id,
            "realized_pnl": pnl,
            "result": result,
            "r_multiple": record["r_multiple"],
            "tags": record["tags"],
            "reason": "OUTCOME_VALIDATED_NOT_PERSISTED",
        }
        if self.store is None:
            return response
        if self.store.add(record):
            response["reason"] = "OUTCOME_RECORDED"
            return response
        existing = self.store.get(trade_id)
        if existing is not None and self._same_outcome(existing, record):
            response["reason"] = "DUPLICATE_OUTCOME_IGNORED"
            return response
        return {
            "accepted": False,
            "trade_id": trade_id,
            "reason": "CONFLICTING_OUTCOME",
            "detail": "a different outcome is already recorded for this trade_id; it was not overwritten",
        }

    @staticmethod
    def _same_outcome(a: Mapping[str, Any], b: Mapping[str, Any]) -> bool:
        keys = ("realized_pnl", "opened_at", "closed_at", "risk_amount", "tags")
        return all(a.get(k) == b.get(k) for k in keys)

    @staticmethod
    def _build_record(outcome: Mapping[str, Any], trade_id: str, pnl: float) -> dict[str, Any]:
        opened = closed = None
        if outcome.get("opened_at") is not None:
            opened = parse_timestamp(outcome["opened_at"])
        if outcome.get("closed_at") is not None:
            closed = parse_timestamp(outcome["closed_at"])
        if opened is not None and closed is not None and closed < opened:
            raise _Invalid("closed_at is earlier than opened_at")
        risk_amount = None
        if outcome.get("risk_amount") is not None:
            risk_amount = _finite(outcome["risk_amount"], "risk_amount")
            if risk_amount <= 0:
                raise _Invalid("risk_amount must be positive")
        tags: dict[str, Any] = {}
        for key in _TAG_KEYS:
            value = outcome.get(key)
            if value is None:
                continue
            if key == "side":
                value = str(value).upper()
                if value not in _ALLOWED_SIDES:
                    raise _Invalid("side must be BUY or SELL")
            elif key == "score":
                value = round(_finite(value, "score"), 4)
            elif key == "forecast_confirmed":
                if not isinstance(value, bool):
                    raise _Invalid("forecast_confirmed must be a boolean")
            else:
                value = str(value)
            tags[key] = value
        return {
            "trade_id": trade_id,
            "realized_pnl": pnl,
            "opened_at": opened,
            "closed_at": closed,
            "risk_amount": risk_amount,
            "r_multiple": None if risk_amount is None else round(pnl / risk_amount, 6),
            "tags": tags,
        }

    # ------------------------------------------------------------------
    # Evidence
    # ------------------------------------------------------------------

    def _records(self, as_of: Any) -> tuple[list[Mapping[str, Any]], int]:
        if self.store is None:
            raise RuntimeError("Learning has no outcome store; construct it with store=... to analyse outcomes")
        records = self.store.all()
        if as_of is None:
            return records, 0
        cutoff = parse_timestamp(as_of)
        kept = [r for r in records if r.get("closed_at") is not None and r["closed_at"] <= cutoff]
        return kept, len(records) - len(kept)

    @staticmethod
    def _stats(records: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
        ordered = sorted(
            records, key=lambda r: (r["closed_at"] is None, r["closed_at"] if r["closed_at"] is not None else 0.0)
        )
        pnls = [r["realized_pnl"] for r in ordered]
        n = len(pnls)
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]
        gross_profit, gross_loss = sum(wins), -sum(losses)
        low, high = _wilson(len(wins), n)
        equity = peak = drawdown = 0.0
        streak = longest = 0
        for p in pnls:
            equity += p
            peak = max(peak, equity)
            drawdown = max(drawdown, peak - equity)
            streak = streak + 1 if p < 0 else 0
            longest = max(longest, streak)
        avg_win = gross_profit / len(wins) if wins else None
        avg_loss = gross_loss / len(losses) if losses else None
        r_values = [r["r_multiple"] for r in ordered if r["r_multiple"] is not None]
        r_bounds = _mean_bounds(r_values, _Z_OVERALL)
        return {
            "trades": n,
            "wins": len(wins),
            "losses": len(losses),
            "breakeven": n - len(wins) - len(losses),
            "win_rate": len(wins) / n if n else None,
            "win_rate_ci95": [round(low, 4), round(high, 4)] if n else None,
            "gross_profit": gross_profit,
            "gross_loss": gross_loss,
            "profit_factor": None if gross_loss == 0 else gross_profit / gross_loss,
            "expectancy": sum(pnls) / n if n else None,
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "payoff_ratio": None if avg_win is None or not avg_loss else avg_win / avg_loss,
            "breakeven_win_rate": None if avg_win is None or avg_loss is None else avg_loss / (avg_win + avg_loss),
            "net_pnl": sum(pnls),
            "max_drawdown": drawdown,
            "max_consecutive_losses": longest,
            "trades_with_r": len(r_values),
            "avg_r": None if r_bounds is None else round(r_bounds[0], 4),
        }

    def summary(self, as_of: Any = None) -> dict[str, Any]:
        """Performance of recorded outcomes, optionally only those closed on or before ``as_of``."""
        records, excluded = self._records(as_of)
        result = {"overall": self._stats(records), "excluded_without_close_time": excluded, "as_of": as_of}
        for key in _SEGMENT_KEYS:
            groups: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
            for record in records:
                if key in record["tags"]:
                    groups[str(record["tags"][key])].append(record)
            result[f"by_{key}"] = {name: self._stats(group) for name, group in sorted(groups.items())}
        return result

    def review(self, as_of: Any = None, min_samples: int = 30) -> dict[str, Any]:
        """Advisory findings. Never applied automatically; small samples yield no conclusions."""
        if min_samples < 10:
            raise ValueError("min_samples must be at least 10")
        records, excluded = self._records(as_of)
        n = len(records)
        base = {"advisory": True, "auto_apply": False, "trades": n, "excluded_without_close_time": excluded}
        if n < min_samples:
            return {
                **base,
                "sufficient_data": False,
                "findings": [{
                    "code": "INSUFFICIENT_DATA", "severity": "INFO", "advisory": True,
                    "detail": f"{n} closed trades; at least {min_samples} are needed before drawing conclusions",
                }],
            }
        findings: list[dict[str, Any]] = []
        stats = self._stats(records)

        def metric(group: list[Mapping[str, Any]], z: float) -> tuple[str, tuple[float, float, float] | None]:
            r_values = [r["r_multiple"] for r in group if r["r_multiple"] is not None]
            if len(r_values) == len(group):  # prefer risk-normalised results when all trades have them
                return "R", _mean_bounds(r_values, z)
            return "PNL", _mean_bounds([r["realized_pnl"] for r in group], z)

        unit, bounds = metric(records, _Z_OVERALL)
        if bounds is not None:
            mean, low, high = bounds
            if high < 0:
                findings.append({
                    "code": "NEGATIVE_EXPECTANCY_SIGNIFICANT", "severity": "WARNING", "advisory": True,
                    "detail": f"mean result per trade is negative with 95% confidence ({unit}: {mean:.3f}, upper bound {high:.3f})",
                    "evidence": {"unit": unit, "mean": mean, "upper95": high, "trades": n},
                })
            elif low <= 0 <= high:
                findings.append({
                    "code": "EXPECTANCY_NOT_DISTINGUISHABLE_FROM_ZERO", "severity": "INFO", "advisory": True,
                    "detail": f"mean {unit} per trade is {mean:.3f} with a 95% interval of [{low:.3f}, {high:.3f}]; "
                              "the sample cannot show an edge",
                    "evidence": {"unit": unit, "mean": mean, "lower95": low, "upper95": high, "trades": n},
                })
        win_rate, breakeven = stats["win_rate"], stats["breakeven_win_rate"]
        if breakeven is not None and win_rate is not None and win_rate >= 0.5 and win_rate < breakeven:
            findings.append({
                "code": "WIN_RATE_BELOW_PAYOFF_BREAKEVEN", "severity": "WARNING", "advisory": True,
                "detail": f"win rate {win_rate:.1%} looks healthy but the average loss is larger than the average win, "
                          f"so {breakeven:.1%} would be needed to break even",
                "evidence": {"win_rate": win_rate, "breakeven_win_rate": breakeven, "payoff_ratio": stats["payoff_ratio"]},
            })
        for key in _SEGMENT_KEYS:
            groups: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
            for record in records:
                if key in record["tags"]:
                    groups[str(record["tags"][key])].append(record)
            for name, group in sorted(groups.items()):
                if len(group) < min_samples or len(group) == n:
                    continue
                seg_unit, seg_bounds = metric(group, _Z_SEGMENT)
                if seg_bounds is not None and seg_bounds[2] < 0:
                    findings.append({
                        "code": "SEGMENT_UNDERPERFORMS", "severity": "WARNING", "advisory": True,
                        "detail": f"{key}={name} loses with 99% confidence ({seg_unit} mean {seg_bounds[0]:.3f} over {len(group)} trades); "
                                  "treat as a hypothesis to test out-of-sample, not a rule",
                        "evidence": {"segment": {key: name}, "unit": seg_unit, "mean": seg_bounds[0],
                                     "upper99": seg_bounds[2], "trades": len(group)},
                    })
        return {**base, "sufficient_data": True, "findings": findings, "stats": stats}
