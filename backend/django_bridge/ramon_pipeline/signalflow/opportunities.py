"""Stage 1: identify trading opportunities using market structure and optional ARIMA/SARIMA evidence.

Two input shapes are supported:

* **Structural (preferred)** - ``market["bars"]`` holds completed, chronologically
  ordered OHLC candles. The detector classifies the market regime (bullish, bearish,
  ranging or uncertain) and looks for a trend-following pullback: an impulse leg in
  the trend direction, a retracement into the EMA value zone, and a confirmed
  resumption. Trend (EMA stack + confirmed swing structure), trend strength (ADX),
  momentum (RSI) and volatility (ATR vs. its own baseline) are combined into a
  0..1 score, and every decision carries the checks and reason behind it.
* **Legacy** - ``market["trend"]`` + ``market["setup_score"]`` (as produced by the
  Django decision endpoint). Behaviour and reason codes are unchanged.

Every result is a dict that always contains ``approved``, ``side``, ``score`` and
``reason`` (what ``entry_timing.py``, ``risk_management.py`` and ``pipeline.py``
read) plus ``status`` (``ACCEPTED`` / ``REJECTED`` / ``DEFERRED``) and an
``explanation``. Status semantics:

* ``ACCEPTED`` - the setup deserves further evaluation by the later stages.
* ``REJECTED`` - the data or setup is invalid, unsuitable or contradicted; waiting
  on the same setup will not help (no trend, broken structure, forecast conflict...).
* ``DEFERRED`` - a valid trend exists but the setup is not ready yet (pullback still
  developing, no resumption, volatility spike...). Re-evaluate on a later bar.

This module only judges whether a setup deserves attention. It never chooses an
entry price, order type, stop, target or volume - those belong to ``entry_timing.py``
and ``risk_management.py``. Indicators are causal and swing pivots are only used once
confirmed by later bars, so a result for bar *t* never depends on bars after *t*.

Statistical forecasts are opt-in because the current forecasting functions perform
rolling backtests and can be computationally expensive. A forecast is only consulted
after a technical setup has already passed every gate, it can veto an opportunity
but can never create one, and it never enables order execution.
"""
import hashlib
import logging
import math
import threading
from collections import OrderedDict
from dataclasses import dataclass
from statistics import median
from typing import Any, Callable, Mapping, Sequence

from .common import ACCEPTED, DEFERRED, REJECTED, InvalidMarketData, clip, finite_number, parse_timestamp

logger = logging.getLogger(__name__)

_NAN = float("nan")

# Relative importance of each score component; must sum to 1.0.
_WEIGHTS = {
    "trend": 0.25,
    "strength": 0.20,
    "pullback": 0.25,
    "momentum": 0.15,
    "volatility": 0.15,
}


@dataclass(frozen=True)
class OpportunityConfig:
    """Tunable thresholds for the trend-following pullback strategy.

    All distances are expressed in ATR so the same defaults scale with volatility.
    The defaults are conventional starting priors, not values optimised on data;
    validate them out-of-sample before relying on them.
    """

    # Data requirements
    min_bars: int = 100
    max_bars: int = 500
    max_gap_intervals: float = 8.0  # largest tolerated hole in the recent bars
    gap_window: int = 40
    symbol_prefixes: tuple[str, ...] | None = ("XAUUSD",)  # None disables the check
    # Indicators
    fast_ema: int = 20
    slow_ema: int = 50
    atr_period: int = 14
    adx_period: int = 14
    rsi_period: int = 14
    pivot_window: int = 3  # bars on each side needed to confirm a swing point
    slope_lookback: int = 10
    # Regime
    adx_trending: float = 22.0
    adx_ranging: float = 18.0
    min_ema_separation_atr: float = 0.3
    support_tolerance_atr: float = 0.5
    regime_lookback: int = 8  # compare with the regime this many bars ago
    # Volatility
    atr_baseline_bars: int = 100
    atr_ratio_min: float = 0.6
    atr_ratio_max: float = 2.5
    # Pullback geometry
    min_impulse_atr: float = 2.0
    min_retracement: float = 0.30
    max_retracement: float = 0.786
    zone_tolerance_atr: float = 0.25
    max_bars_since_pullback_low: int = 6
    rsi_overbought: float = 70.0  # mirrored (100 - x) for shorts

    def __post_init__(self) -> None:
        if self.pivot_window < 1:
            raise ValueError("pivot_window must be at least 1")
        if not 1 <= self.fast_ema < self.slow_ema:
            raise ValueError("fast_ema must be positive and smaller than slow_ema")
        for name in ("atr_period", "adx_period", "rsi_period", "slope_lookback", "regime_lookback"):
            if getattr(self, name) < 1:
                raise ValueError(f"{name} must be positive")
        if self.min_bars < max(2 * self.slow_ema, 3 * self.adx_period, self.atr_baseline_bars // 2):
            raise ValueError("min_bars is too small for the configured indicators")
        if self.max_bars < self.min_bars:
            raise ValueError("max_bars must be at least min_bars")
        if not 0.0 < self.min_retracement < self.max_retracement < 1.0:
            raise ValueError("retracement bounds must satisfy 0 < min < max < 1")
        if not 0.0 < self.adx_ranging <= self.adx_trending:
            raise ValueError("adx_ranging must be positive and not above adx_trending")
        if not 0.0 < self.atr_ratio_min < self.atr_ratio_max:
            raise ValueError("atr ratio bounds must satisfy 0 < min < max")


@dataclass(frozen=True)
class _Bars:
    times: list[float]
    high: list[float]
    low: list[float]
    close: list[float]
    interval: float  # median spacing between bars, seconds


@dataclass(frozen=True)
class _Indicators:
    ema_fast: list[float]
    ema_slow: list[float]
    atr: list[float]
    adx: list[float]
    rsi: list[float]
    swing_highs: list[int]
    swing_lows: list[int]


@dataclass(frozen=True)
class _Regime:
    label: str  # BULLISH | BEARISH | RANGING | UNCERTAIN
    ema_bias: str  # BULL | BEAR | NEUTRAL
    structure: str  # BULLISH | BEARISH | MIXED | UNKNOWN
    adx: float
    separation_atr: float
    slope_atr: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "ema_bias": self.ema_bias,
            "structure": self.structure,
            "adx": round(self.adx, 2),
            "ema_separation_atr": round(self.separation_atr, 3),
            "ema_slope_atr": round(self.slope_atr, 3),
        }


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------

def _time_out(value: float) -> int | float:
    return int(value) if float(value).is_integer() else value


def _parse_bars(raw: Any, config: OpportunityConfig) -> _Bars:
    if isinstance(raw, (str, bytes)) or not isinstance(raw, Sequence):
        raise InvalidMarketData("INVALID_MARKET_DATA", "bars must be a list of candle objects")
    if len(raw) < config.min_bars:
        raise InvalidMarketData(
            "INSUFFICIENT_BARS", f"{len(raw)} bars supplied, at least {config.min_bars} required"
        )
    times: list[float] = []
    high: list[float] = []
    low: list[float] = []
    close: list[float] = []
    for index, bar in enumerate(raw[-config.max_bars:]):
        if not isinstance(bar, Mapping):
            raise InvalidMarketData("INVALID_MARKET_DATA", f"bar {index} must be an object")
        try:
            t = parse_timestamp(bar["time"])
            h = finite_number(bar["high"], "high")
            l = finite_number(bar["low"], "low")
            c = finite_number(bar["close"], "close")
        except KeyError as exc:
            raise InvalidMarketData("INVALID_MARKET_DATA", f"bar {index} is missing {exc.args[0]}") from None
        except InvalidMarketData as exc:
            raise InvalidMarketData(exc.reason, f"bar {index}: {exc.detail}") from None
        if l <= 0 or h < l:
            raise InvalidMarketData("INVALID_MARKET_DATA", f"bar {index} has an invalid high/low range")
        slack = 1e-9 * h
        if c < l - slack or c > h + slack:
            raise InvalidMarketData("INVALID_MARKET_DATA", f"bar {index} closes outside its high/low range")
        if times and t <= times[-1]:
            raise InvalidMarketData("INVALID_MARKET_DATA", f"bar {index} is not newer than the previous bar")
        times.append(t)
        high.append(h)
        low.append(l)
        close.append(c)
    gaps = [b - a for a, b in zip(times, times[1:])]
    interval = median(gaps)
    recent = gaps[-config.gap_window:]
    if recent and max(recent) > config.max_gap_intervals * interval:
        raise InvalidMarketData(
            "DATA_GAP_IN_RECENT_BARS",
            f"a gap of {max(recent) / interval:.1f} bar intervals exists in the last {len(recent)} bars",
        )
    return _Bars(times, high, low, close, interval)


# ---------------------------------------------------------------------------
# Causal indicators (value at index i only uses bars 0..i)
# ---------------------------------------------------------------------------

def _ema(values: Sequence[float], period: int) -> list[float]:
    alpha = 2.0 / (period + 1)
    out = [values[0]]
    for value in values[1:]:
        out.append(alpha * value + (1.0 - alpha) * out[-1])
    return out


def _true_range(high: Sequence[float], low: Sequence[float], close: Sequence[float]) -> list[float]:
    tr = [high[0] - low[0]]
    for i in range(1, len(close)):
        tr.append(max(high[i] - low[i], abs(high[i] - close[i - 1]), abs(low[i] - close[i - 1])))
    return tr


def _atr(tr: Sequence[float], period: int) -> list[float]:
    out = [_NAN] * len(tr)
    if len(tr) < period:
        return out
    value = sum(tr[:period]) / period
    out[period - 1] = value
    for i in range(period, len(tr)):
        value = (value * (period - 1) + tr[i]) / period
        out[i] = value
    return out


def _rsi_from(avg_gain: float, avg_loss: float) -> float:
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    return 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)


def _rsi(close: Sequence[float], period: int) -> list[float]:
    out = [_NAN] * len(close)
    if len(close) <= period:
        return out
    gains = losses = 0.0
    for i in range(1, period + 1):
        change = close[i] - close[i - 1]
        gains += max(change, 0.0)
        losses += max(-change, 0.0)
    avg_gain, avg_loss = gains / period, losses / period
    out[period] = _rsi_from(avg_gain, avg_loss)
    for i in range(period + 1, len(close)):
        change = close[i] - close[i - 1]
        avg_gain = (avg_gain * (period - 1) + max(change, 0.0)) / period
        avg_loss = (avg_loss * (period - 1) + max(-change, 0.0)) / period
        out[i] = _rsi_from(avg_gain, avg_loss)
    return out


def _adx(high: Sequence[float], low: Sequence[float], close: Sequence[float], period: int) -> list[float]:
    n = len(close)
    out = [_NAN] * n
    if n < 2 * period + 1:
        return out
    tr = _true_range(high, low, close)
    plus_dm = [0.0] * n
    minus_dm = [0.0] * n
    for i in range(1, n):
        up, down = high[i] - high[i - 1], low[i - 1] - low[i]
        plus_dm[i] = up if up > down and up > 0 else 0.0
        minus_dm[i] = down if down > up and down > 0 else 0.0
    tr_sum = sum(tr[1:period + 1])
    plus_sum = sum(plus_dm[1:period + 1])
    minus_sum = sum(minus_dm[1:period + 1])
    dx = [_NAN] * n
    for i in range(period, n):
        if i > period:
            tr_sum += tr[i] - tr_sum / period
            plus_sum += plus_dm[i] - plus_sum / period
            minus_sum += minus_dm[i] - minus_sum / period
        if tr_sum <= 0:
            dx[i] = 0.0
            continue
        plus_di, minus_di = 100.0 * plus_sum / tr_sum, 100.0 * minus_sum / tr_sum
        total = plus_di + minus_di
        dx[i] = 100.0 * abs(plus_di - minus_di) / total if total > 0 else 0.0
    first = 2 * period - 1
    value = sum(dx[period:first + 1]) / period
    out[first] = value
    for i in range(first + 1, n):
        value = (value * (period - 1) + dx[i]) / period
        out[i] = value
    return out


def _pivots(high: Sequence[float], low: Sequence[float], window: int) -> tuple[list[int], list[int]]:
    """Confirmed fractal swing highs/lows.

    A pivot at ``i`` needs ``window`` bars on each side, so the newest ``window`` bars
    can never be pivots. That is what keeps pivot logic free of look-ahead: a pivot is
    only known once ``window`` later bars have closed.
    """
    swing_highs: list[int] = []
    swing_lows: list[int] = []
    for i in range(window, len(high) - window):
        left_h, right_h = high[i - window:i], high[i + 1:i + window + 1]
        if high[i] > max(left_h) and high[i] >= max(right_h):
            swing_highs.append(i)
        left_l, right_l = low[i - window:i], low[i + 1:i + window + 1]
        if low[i] < min(left_l) and low[i] <= min(right_l):
            swing_lows.append(i)
    return swing_highs, swing_lows


def _compute_indicators(bars: _Bars, config: OpportunityConfig) -> _Indicators:
    tr = _true_range(bars.high, bars.low, bars.close)
    swing_highs, swing_lows = _pivots(bars.high, bars.low, config.pivot_window)
    return _Indicators(
        ema_fast=_ema(bars.close, config.fast_ema),
        ema_slow=_ema(bars.close, config.slow_ema),
        atr=_atr(tr, config.atr_period),
        adx=_adx(bars.high, bars.low, bars.close, config.adx_period),
        rsi=_rsi(bars.close, config.rsi_period),
        swing_highs=swing_highs,
        swing_lows=swing_lows,
    )


# ---------------------------------------------------------------------------
# Regime classification
# ---------------------------------------------------------------------------

def _structure(bars: _Bars, swing_highs: list[int], swing_lows: list[int]) -> str:
    if len(swing_highs) < 2 or len(swing_lows) < 2:
        return "UNKNOWN"
    higher_high = bars.high[swing_highs[-1]] > bars.high[swing_highs[-2]]
    higher_low = bars.low[swing_lows[-1]] > bars.low[swing_lows[-2]]
    lower_high = bars.high[swing_highs[-1]] < bars.high[swing_highs[-2]]
    lower_low = bars.low[swing_lows[-1]] < bars.low[swing_lows[-2]]
    if higher_high and higher_low:
        return "BULLISH"
    if lower_high and lower_low:
        return "BEARISH"
    return "MIXED"


def _classify_regime(bars: _Bars, ind: _Indicators, t: int, config: OpportunityConfig) -> _Regime:
    """Classify the market as of bar ``t`` using only information available at ``t``."""
    atr = ind.atr[t]
    close = bars.close[t]
    separation = (ind.ema_fast[t] - ind.ema_slow[t]) / atr
    slope = (ind.ema_slow[t] - ind.ema_slow[t - config.slope_lookback]) / atr
    adx = ind.adx[t]
    known = config.pivot_window  # pivots newer than t - window were not yet confirmed at t
    highs = [i for i in ind.swing_highs if i + known <= t]
    lows = [i for i in ind.swing_lows if i + known <= t]
    structure = _structure(bars, highs, lows)
    support = config.support_tolerance_atr * atr

    if separation >= config.min_ema_separation_atr and close > ind.ema_slow[t] - support and slope > 0:
        bias = "BULL"
    elif separation <= -config.min_ema_separation_atr and close < ind.ema_slow[t] + support and slope < 0:
        bias = "BEAR"
    else:
        bias = "NEUTRAL"

    trending = adx >= config.adx_trending
    if bias == "BULL" and structure != "BEARISH" and trending:
        label = "BULLISH"
    elif bias == "BEAR" and structure != "BULLISH" and trending:
        label = "BEARISH"
    elif adx < config.adx_ranging or (bias == "NEUTRAL" and structure in ("MIXED", "UNKNOWN") and not trending):
        label = "RANGING"
    else:
        label = "UNCERTAIN"
    return _Regime(label, bias, structure, adx, separation, slope)


# ---------------------------------------------------------------------------
# Score components (each 0..1, oriented to the trade direction)
# ---------------------------------------------------------------------------

def _trend_component(sign: int, regime: _Regime) -> float:
    if regime.ema_bias != ("BULL" if sign > 0 else "BEAR"):
        return 0.0
    wanted = "BULLISH" if sign > 0 else "BEARISH"
    structure = 1.0 if regime.structure == wanted else 0.5 if regime.structure in ("MIXED", "UNKNOWN") else 0.0
    return 0.5 * clip(abs(regime.separation_atr)) + 0.25 * clip(abs(regime.slope_atr) / 0.5) + 0.25 * structure


def _strength_component(adx: float, config: OpportunityConfig) -> float:
    return clip((adx - config.adx_ranging) / (35.0 - config.adx_ranging))


def _volatility_component(ratio: float, config: OpportunityConfig) -> float:
    if ratio < 0.8:
        return clip((ratio - config.atr_ratio_min) / (0.8 - config.atr_ratio_min))
    if ratio <= 1.6:
        return 1.0
    return clip((config.atr_ratio_max - ratio) / (config.atr_ratio_max - 1.6))


def _pullback_component(retracement: float, zone_gap_atr: float, config: OpportunityConfig) -> float:
    depth = 1.0 - clip(abs(retracement - 0.5) / 0.35)
    zone = 1.0 - 0.5 * clip(zone_gap_atr / config.zone_tolerance_atr)
    return 0.6 * depth + 0.4 * zone


def _momentum_component(rsi_dir: float, bounce_atr: float) -> float:
    if rsi_dir < 45:
        rsi_score = clip((rsi_dir - 30.0) / 15.0)
    elif rsi_dir <= 65:
        rsi_score = 1.0
    else:
        rsi_score = clip((75.0 - rsi_dir) / 10.0)
    return 0.7 * rsi_score + 0.3 * clip(bounce_atr)


def _weighted_score(components: Mapping[str, float]) -> float:
    return sum(_WEIGHTS[name] * components.get(name, 0.0) for name in _WEIGHTS)


# ---------------------------------------------------------------------------
# Result helpers
# ---------------------------------------------------------------------------

def _check(checks: list[dict[str, Any]], name: str, passed: bool, detail: str) -> bool:
    checks.append({"name": name, "passed": passed, "detail": detail})
    return passed


def _forecast_status(reason: str) -> str:
    return DEFERRED if reason in {"FORECAST_UNAVAILABLE", "FORECAST_NOT_CURRENT"} else REJECTED


class OpportunityDetector:
    def __init__(
        self,
        minimum_score: float = 0.65,
        *,
        use_statistical_forecasts: bool = False,
        forecast_limit: int = 200,
        arima_order: tuple[int, int, int] = (1, 1, 1),
        sarima_order: tuple[int, int, int] = (1, 1, 1),
        sarima_seasonal_order: tuple[int, int, int, int] = (1, 0, 1, 5),
        config: OpportunityConfig | None = None,
    ) -> None:
        if not 0.0 <= minimum_score <= 1.0:
            raise ValueError("minimum_score must be between 0 and 1")
        if forecast_limit < 40:
            raise ValueError("forecast_limit must be at least 40 candles")
        self.minimum_score = minimum_score
        self.use_statistical_forecasts = use_statistical_forecasts
        self.forecast_limit = forecast_limit
        self.arima_order = arima_order
        self.sarima_order = sarima_order
        self.sarima_seasonal_order = sarima_seasonal_order
        self.config = config or OpportunityConfig()
        self._lock = threading.Lock()
        # signal key -> signal bar time already accepted (bounded, oldest evicted first)
        self._accepted: OrderedDict[str, float] = OrderedDict()
        # (model, symbol, timeframe, source, params, bar time) -> result or failure
        self._forecast_cache: OrderedDict[tuple, Any] = OrderedDict()

    # ------------------------------------------------------------------
    # Forecast evidence (optional)
    # ------------------------------------------------------------------

    @staticmethod
    def _forecast_direction(result: Any) -> str:
        last_close = float(result.last_close)
        forecast_close = float(result.forecast_close)
        if forecast_close > last_close:
            return "BUY"
        if forecast_close < last_close:
            return "SELL"
        return "HOLD"

    @staticmethod
    def _forecast_quality(result: Any) -> str:
        """Informational only: did the model beat a no-change forecast in its own backtest?"""
        rmse = getattr(result, "rmse", None)
        baseline = getattr(result, "baseline_rmse", None)
        if isinstance(rmse, (int, float)) and isinstance(baseline, (int, float)):
            return "BEATS_PERSISTENCE" if rmse < baseline else "NO_BETTER_THAN_PERSISTENCE"
        return "UNKNOWN"

    @staticmethod
    def _forecast_is_current(result: Any, bar_time: float | None, tolerance: float) -> bool | None:
        """True/False when the forecast's last candle can be compared with the snapshot's, else None."""
        raw = getattr(result, "last_bar_time", None)
        if bar_time is None or raw is None:
            return None
        try:
            return abs(parse_timestamp(raw) - bar_time) <= tolerance
        except InvalidMarketData:
            return False

    def _cached_forecast(self, key: tuple | None, compute: Callable[[], Any]) -> Any:
        """Compute a forecast at most once per (model, series, bar); failures are cached too."""
        if key is not None:
            with self._lock:
                if key in self._forecast_cache:
                    self._forecast_cache.move_to_end(key)
                    cached = self._forecast_cache[key]
                    if isinstance(cached, Exception):
                        raise cached
                    return cached
        try:
            value: Any = compute()
        except Exception as exc:
            value = exc
        if key is not None:
            with self._lock:
                self._forecast_cache[key] = value
                while len(self._forecast_cache) > 16:
                    self._forecast_cache.popitem(last=False)
        if isinstance(value, Exception):
            raise value
        return value

    def _evaluate_forecasts(
        self,
        market: Mapping[str, Any],
        side: str,
        *,
        bar_time: float | None = None,
        tolerance: float = 0.0,
    ) -> dict[str, Any]:
        """Run opt-in statistical forecasts and require directional agreement.

        This currently invokes rolling-evaluation functions, so callers should
        enable it only in a worker/research path, not a latency-sensitive EA
        heartbeat or order-execution request. SARIMA is skipped when ARIMA already
        disagrees, and results are cached per bar so repeated evaluation of the
        same bar never refits the models.
        """
        symbol = str(market.get("symbol", "")).strip()
        timeframe = str(market.get("timeframe", "M15")).upper()
        source_value = market.get("source")
        source = str(source_value) if source_value else None
        if not symbol:
            return {"approved": False, "reason": "FORECAST_SYMBOL_REQUIRED"}

        def key(model: str, params: tuple) -> tuple | None:
            if bar_time is None:
                return None
            return (model, symbol, timeframe, source, self.forecast_limit, params, bar_time)

        def failure(reason: str, exc: Exception) -> dict[str, Any]:
            detail = str(exc) if reason == "FORECAST_UNAVAILABLE" else f"{type(exc).__name__}: {exc}"
            return {"approved": False, "reason": reason, "detail": detail}

        def not_current(model: str, result: Any) -> dict[str, Any]:
            return {
                "approved": False,
                "reason": "FORECAST_NOT_CURRENT",
                "detail": f"{model} forecast was built on a different last candle "
                          f"({getattr(result, 'last_bar_time', None)}) than the evaluated snapshot",
            }

        try:
            # Lazy imports keep statsmodels and model fitting out of the default path.
            from .forecasting.arima import forecast_market_bars
            from .forecasting.sarima import forecast_market_bars_sarima

            arima = self._cached_forecast(
                key("arima", (self.arima_order,)),
                lambda: forecast_market_bars(
                    symbol=symbol,
                    timeframe=timeframe,
                    source=source,
                    limit=self.forecast_limit,
                    order=self.arima_order,
                ),
            )
            if self._forecast_is_current(arima, bar_time, tolerance) is False:
                return not_current("ARIMA", arima)
            arima_side = self._forecast_direction(arima)
            if arima_side != side:
                # SARIMA is the expensive model; the veto is already decided.
                return {
                    "approved": False,
                    "reason": "FORECAST_DIRECTION_MISMATCH",
                    "side": "HOLD",
                    "arima": arima.as_dict(),
                    "sarima": None,
                    "arima_direction": arima_side,
                    "sarima_direction": "NOT_EVALUATED",
                    "arima_quality": self._forecast_quality(arima),
                }
            sarima = self._cached_forecast(
                key("sarima", (self.sarima_order, self.sarima_seasonal_order)),
                lambda: forecast_market_bars_sarima(
                    symbol=symbol,
                    timeframe=timeframe,
                    source=source,
                    limit=self.forecast_limit,
                    order=self.sarima_order,
                    seasonal_order=self.sarima_seasonal_order,
                ),
            )
            if self._forecast_is_current(sarima, bar_time, tolerance) is False:
                return not_current("SARIMA", sarima)
        except (ValueError, RuntimeError, ArithmeticError) as exc:
            return failure("FORECAST_UNAVAILABLE", exc)
        except Exception as exc:
            # Forecast failures must not silently approve a trade.
            return failure("FORECAST_ERROR", exc)

        sarima_side = self._forecast_direction(sarima)
        agreed = sarima_side == side and side in {"BUY", "SELL"}
        return {
            "approved": agreed,
            "reason": "FORECASTS_AGREE" if agreed else "FORECAST_DIRECTION_MISMATCH",
            "side": side if agreed else "HOLD",
            "arima": arima.as_dict(),
            "sarima": sarima.as_dict(),
            "arima_direction": arima_side,
            "sarima_direction": sarima_side,
            "arima_quality": self._forecast_quality(arima),
            "sarima_quality": self._forecast_quality(sarima),
        }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def evaluate(self, market: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(market, Mapping):
            return self._result(REJECTED, "INVALID_MARKET_SNAPSHOT", detail="market snapshot must be a mapping")
        if market.get("bars") is not None:
            try:
                return self._evaluate_structure(market)
            except Exception as exc:  # fail closed: never let a bug approve or crash the pipeline
                logger.exception("Opportunity evaluation failed")
                return self._result(
                    REJECTED, "OPPORTUNITY_EVALUATION_ERROR", detail=f"{type(exc).__name__}: {exc}"
                )
        return self._evaluate_legacy(market)

    # ------------------------------------------------------------------
    # Legacy path: trend + setup_score supplied by the caller
    # ------------------------------------------------------------------

    def _evaluate_legacy(self, market: Mapping[str, Any]) -> dict[str, Any]:
        side = str(market.get("trend", "")).upper()
        try:
            score = float(market.get("setup_score", 0.0))
        except (TypeError, ValueError):
            score = 0.0

        def rejected(reason: str, explanation: str) -> dict[str, Any]:
            return {
                "approved": False,
                "status": REJECTED,
                "side": "HOLD",
                "score": score,
                "reason": reason,
                "explanation": explanation,
            }

        if side not in {"BUY", "SELL"}:
            return rejected("NO_VALID_DIRECTION", "trend is not BUY or SELL")
        if not 0.0 <= score <= 1.0:
            return rejected("INVALID_SETUP_SCORE", "setup_score must be between 0 and 1")
        if score < self.minimum_score:
            return rejected(
                "SETUP_SCORE_TOO_LOW", f"setup_score {score:.2f} is below the minimum {self.minimum_score:.2f}"
            )

        result: dict[str, Any] = {
            "approved": True,
            "status": ACCEPTED,
            "side": side,
            "score": score,
            "reason": "OPPORTUNITY_FOUND",
            "explanation": f"{side} accepted from the supplied trend and setup_score {score:.2f}",
        }
        if self.use_statistical_forecasts or bool(market.get("use_statistical_forecasts", False)):
            forecast_result = self._evaluate_forecasts(market, side)
            result["forecasting"] = forecast_result
            if not forecast_result.get("approved", False):
                reason = forecast_result.get("reason", "FORECAST_REJECTED")
                result.update(
                    approved=False,
                    status=_forecast_status(reason),
                    side="HOLD",
                    reason=reason,
                    explanation=f"{side} vetoed by statistical forecasts: {reason}",
                )
            else:
                result["reason"] = "OPPORTUNITY_FOUND_WITH_FORECAST_CONFIRMATION"
        return result

    # ------------------------------------------------------------------
    # Structural path: trend-following pullback on completed candles
    # ------------------------------------------------------------------

    @staticmethod
    def _result(
        status: str,
        reason: str,
        *,
        detail: str = "",
        side: str = "HOLD",
        candidate_side: str = "NONE",
        score: float = 0.0,
        **extra: Any,
    ) -> dict[str, Any]:
        approved = status == ACCEPTED
        explanation = extra.pop("explanation", None)
        if explanation is None:
            subject = f"{candidate_side} setup" if candidate_side != "NONE" else "No setup"
            explanation = f"{subject} {status.lower()}: {detail or reason}"
        result: dict[str, Any] = {
            "approved": approved,
            "status": status,
            "side": side if approved else "HOLD",
            "candidate_side": candidate_side,
            "score": round(score, 4),
            "reason": reason,
            "explanation": explanation,
            "regime": None,
            "components": {},
            "checks": [],
            "signal_bar_time": None,
        }
        result.update(extra)
        return result

    def _evaluate_structure(self, market: Mapping[str, Any]) -> dict[str, Any]:
        config = self.config
        checks: list[dict[str, Any]] = []
        symbol = str(market.get("symbol", "")).strip()
        timeframe = str(market.get("timeframe", "M15")).upper()

        def done(status: str, reason: str, **kwargs: Any) -> dict[str, Any]:
            return self._result(status, reason, checks=checks, **kwargs)

        if symbol and config.symbol_prefixes and not symbol.upper().startswith(
            tuple(prefix.upper() for prefix in config.symbol_prefixes)
        ):
            _check(checks, "symbol", False, f"{symbol} is not supported by this strategy")
            return done(REJECTED, "UNSUPPORTED_SYMBOL", detail=f"{symbol} is not an XAUUSD instrument")

        try:
            bars = _parse_bars(market["bars"], config)
        except InvalidMarketData as exc:
            _check(checks, "data_valid", False, exc.detail)
            return done(REJECTED, exc.reason, detail=exc.detail)
        _check(checks, "data_valid", True, f"{len(bars.close)} completed bars")

        if market.get("last_bar_closed") is False:
            _check(checks, "bar_complete", False, "the newest candle is still forming")
            return done(DEFERRED, "LAST_BAR_NOT_CLOSED", detail="the newest candle is still forming")

        t = len(bars.close) - 1
        ind = _compute_indicators(bars, config)
        atr = ind.atr[t]
        if not all(math.isfinite(x) for x in (atr, ind.adx[t], ind.rsi[t])) or atr <= 0:
            detail = "indicators are not computable (flat or degenerate price data)"
            _check(checks, "data_valid", False, detail)
            return done(REJECTED, "INVALID_MARKET_DATA", detail=detail)

        bar_time = bars.times[t]
        regime = _classify_regime(bars, ind, t, config)
        previous = _classify_regime(bars, ind, t - config.regime_lookback, config)
        regime_info = {
            **regime.as_dict(),
            "previous_label": previous.label,
            "regime_changed": previous.label != regime.label,
        }
        components: dict[str, float] = {}
        baseline = median(x for x in ind.atr[-config.atr_baseline_bars:] if math.isfinite(x))
        atr_ratio = atr / baseline
        components["strength"] = _strength_component(regime.adx, config)
        components["volatility"] = _volatility_component(atr_ratio, config)

        def finish(status: str, reason: str, detail: str, side: str = "NONE", **kwargs: Any) -> dict[str, Any]:
            return done(
                status,
                reason,
                detail=detail,
                candidate_side=side,
                score=_weighted_score(components),
                regime=regime_info,
                components={k: round(v, 4) for k, v in components.items()},
                signal_bar_time=_time_out(bar_time),
                **kwargs,
            )

        # 1. Regime ---------------------------------------------------------
        regime_text = (
            f"{regime.label} (ADX {regime.adx:.1f}, EMA bias {regime.ema_bias}, structure {regime.structure})"
        )
        if regime.label == "RANGING":
            _check(checks, "regime", False, regime_text)
            return finish(REJECTED, "MARKET_RANGING", f"market is ranging: {regime_text}")
        if regime.label == "UNCERTAIN":
            _check(checks, "regime", False, regime_text)
            return finish(DEFERRED, "MARKET_UNCERTAIN", f"trend signals conflict or are unconfirmed: {regime_text}")
        _check(checks, "regime", True, regime_text)

        sign = 1 if regime.label == "BULLISH" else -1
        side = "BUY" if sign > 0 else "SELL"
        components["trend"] = _trend_component(sign, regime)

        opposite = "BEARISH" if sign > 0 else "BULLISH"
        if previous.label == opposite:
            detail = f"regime was {previous.label} {config.regime_lookback} bars ago; reversal not yet established"
            _check(checks, "regime_stability", False, detail)
            return finish(DEFERRED, "REGIME_REVERSAL_UNCONFIRMED", detail, side)
        _check(checks, "regime_stability", True, f"regime {config.regime_lookback} bars ago was {previous.label}")

        # 2. Volatility -----------------------------------------------------
        vol_text = f"ATR is {atr_ratio:.2f}x its recent median"
        if atr_ratio < config.atr_ratio_min:
            _check(checks, "volatility", False, vol_text + " (too quiet)")
            return finish(REJECTED, "VOLATILITY_TOO_LOW", vol_text + ", too quiet to trade", side)
        if atr_ratio > config.atr_ratio_max:
            _check(checks, "volatility", False, vol_text + " (spike)")
            return finish(DEFERRED, "VOLATILITY_SPIKE", vol_text + ", wait for volatility to settle", side)
        _check(checks, "volatility", True, vol_text)

        # 3. Pullback geometry, oriented so "up" is the trade direction ---------
        if sign > 0:
            o_high, o_low, o_close = bars.high, bars.low, bars.close
            o_ema_fast, o_ema_slow = ind.ema_fast, ind.ema_slow
            o_swing_highs, o_swing_lows = ind.swing_highs, ind.swing_lows
        else:
            o_high, o_low = [-x for x in bars.low], [-x for x in bars.high]
            o_close = [-x for x in bars.close]
            o_ema_fast, o_ema_slow = [-x for x in ind.ema_fast], [-x for x in ind.ema_slow]
            o_swing_highs, o_swing_lows = ind.swing_lows, ind.swing_highs

        if not o_swing_highs:
            _check(checks, "impulse", False, "no confirmed swing point in the trend direction")
            return finish(REJECTED, "NO_IMPULSE_LEG", "no confirmed swing point in the trend direction", side)
        ih = o_swing_highs[-1]
        starts = [i for i in o_swing_lows if i < ih]
        if not starts:
            _check(checks, "impulse", False, "no confirmed swing point before the latest extreme")
            return finish(REJECTED, "NO_IMPULSE_LEG", "no confirmed swing point before the latest extreme", side)
        il = starts[-1]
        if max(o_high[ih + 1:]) >= o_high[ih]:
            detail = "price is still making new extremes; no pullback has started"
            _check(checks, "impulse", False, detail)
            return finish(DEFERRED, "IMPULSE_STILL_EXTENDING", detail, side)
        impulse = o_high[ih] - o_low[il]
        impulse_atr = impulse / atr
        if impulse_atr < config.min_impulse_atr:
            detail = f"impulse leg is {impulse_atr:.1f} ATR, below the {config.min_impulse_atr:.1f} ATR minimum"
            _check(checks, "impulse", False, detail)
            return finish(REJECTED, "IMPULSE_TOO_SMALL", detail, side)
        _check(checks, "impulse", True, f"impulse leg of {impulse_atr:.1f} ATR")

        window = o_low[ih + 1:]
        pullback_low = min(window)
        ip = ih + 1 + window.index(pullback_low)
        retracement = (o_high[ih] - pullback_low) / impulse
        if pullback_low <= o_low[il] or retracement > config.max_retracement:
            detail = f"pullback retraced {retracement:.0%} of the impulse; the trend structure is broken"
            _check(checks, "pullback_depth", False, detail)
            return finish(REJECTED, "PULLBACK_TOO_DEEP", detail, side)
        if retracement < config.min_retracement:
            detail = f"pullback retraced only {retracement:.0%} (minimum {config.min_retracement:.0%})"
            _check(checks, "pullback_depth", False, detail)
            return finish(DEFERRED, "PULLBACK_NOT_DEEP_ENOUGH", detail, side)
        _check(checks, "pullback_depth", True, f"pullback retraced {retracement:.0%} of the impulse")

        support = config.support_tolerance_atr * atr
        if any(o_close[j] < o_ema_slow[j] - support for j in range(ih + 1, t + 1)):
            detail = f"a close broke below the slow EMA by more than {config.support_tolerance_atr} ATR"
            _check(checks, "trend_support", False, detail)
            return finish(REJECTED, "TREND_SUPPORT_BROKEN", detail, side)
        _check(checks, "trend_support", True, "closes held above the slow EMA")

        zone_gap_atr = max(0.0, pullback_low - o_ema_fast[ip]) / atr
        if zone_gap_atr > config.zone_tolerance_atr:
            detail = f"pullback stopped {zone_gap_atr:.2f} ATR above the fast EMA zone"
            _check(checks, "value_zone", False, detail)
            return finish(DEFERRED, "PULLBACK_NOT_IN_VALUE_ZONE", detail, side)
        _check(checks, "value_zone", True, "pullback reached the fast EMA zone")
        components["pullback"] = _pullback_component(retracement, zone_gap_atr, config)

        bars_since_low = t - ip
        if bars_since_low > config.max_bars_since_pullback_low:
            detail = f"pullback low was {bars_since_low} bars ago without a confirmed resumption"
            _check(checks, "freshness", False, detail)
            return finish(REJECTED, "SETUP_STALE", detail, side)
        resumed = bars_since_low >= 1 and o_close[t] > o_ema_fast[t] and o_close[t] > o_close[t - 1]
        if not resumed:
            detail = "pullback has not turned: waiting for a close back above the fast EMA and the prior close"
            _check(checks, "resumption", False, detail)
            return finish(DEFERRED, "AWAITING_RESUMPTION", detail, side)
        _check(checks, "resumption", True, f"closed back above the fast EMA {bars_since_low} bar(s) after the pullback low")

        rsi_dir = ind.rsi[t] if sign > 0 else 100.0 - ind.rsi[t]
        bounce_atr = (o_close[t] - pullback_low) / atr
        components["momentum"] = _momentum_component(rsi_dir, bounce_atr)
        if rsi_dir > config.rsi_overbought:
            detail = f"momentum is overextended (RSI {rsi_dir:.0f} in the trade direction)"
            _check(checks, "momentum", False, detail)
            return finish(DEFERRED, "MOMENTUM_OVEREXTENDED", detail, side)
        _check(checks, "momentum", True, f"RSI {rsi_dir:.0f} in the trade direction")

        # 4. Score threshold ----------------------------------------------------
        score = _weighted_score(components)
        if score < self.minimum_score:
            detail = f"setup score {score:.2f} is below the minimum {self.minimum_score:.2f}"
            _check(checks, "score", False, detail)
            return finish(REJECTED, "SETUP_SCORE_TOO_LOW", detail, side)
        _check(checks, "score", True, f"setup score {score:.2f} >= {self.minimum_score:.2f}")

        # 5. Duplicate suppression: one signal per impulse/pullback ----------------
        anchor_time = bars.times[ih]
        signal_key = f"{symbol}|{timeframe}|{side}|{anchor_time}"
        with self._lock:
            earlier = self._accepted.get(signal_key)
        if earlier is not None and earlier != bar_time:
            detail = "this pullback already produced a signal on an earlier bar"
            _check(checks, "duplicate", False, detail)
            return finish(REJECTED, "DUPLICATE_SIGNAL", detail, side)
        _check(checks, "duplicate", True, "no earlier signal for this pullback")

        setup = {
            "type": "TREND_PULLBACK",
            "side": side,
            "anchor_time": _time_out(anchor_time),
            "impulse_atr": round(impulse_atr, 3),
            "retracement": round(retracement, 4),
            "pullback_extreme_price": round(sign * pullback_low, 5),
            "bars_since_pullback_extreme": bars_since_low,
            "reference_price": bars.close[t],
            "invalidation_price": round(sign * pullback_low, 5),
            "atr": round(atr, 5),
            "rsi": round(ind.rsi[t], 2),
            "atr_ratio": round(atr_ratio, 3),
        }
        extra: dict[str, Any] = {"setup": setup}

        # 6. Optional statistical evidence (only for setups that already passed) ----
        reason = "OPPORTUNITY_FOUND"
        if self.use_statistical_forecasts or bool(market.get("use_statistical_forecasts", False)):
            forecast = self._evaluate_forecasts(
                market, side, bar_time=bar_time, tolerance=bars.interval / 2.0
            )
            extra["forecasting"] = forecast
            if not forecast.get("approved", False):
                reject_reason = forecast.get("reason", "FORECAST_REJECTED")
                _check(checks, "forecast", False, forecast.get("detail", reject_reason))
                return finish(_forecast_status(reject_reason), reject_reason,
                              f"technical setup passed but forecasts do not support it ({reject_reason})",
                              side, **extra)
            _check(checks, "forecast", True, "ARIMA and SARIMA agree with the trade direction")
            reason = "OPPORTUNITY_FOUND_WITH_FORECAST_CONFIRMATION"

        with self._lock:
            self._accepted[signal_key] = bar_time
            self._accepted.move_to_end(signal_key)
            while len(self._accepted) > 512:
                self._accepted.popitem(last=False)
        signal_id = hashlib.sha256(f"{signal_key}|{bar_time}".encode()).hexdigest()[:16]
        explanation = (
            f"{side} accepted: {regime.label.lower()} regime (ADX {regime.adx:.1f}, structure "
            f"{regime.structure.lower()}); pullback retraced {retracement:.0%} of a {impulse_atr:.1f} ATR "
            f"impulse into the EMA zone and price closed back above the fast EMA; RSI {rsi_dir:.0f}, "
            f"ATR {atr_ratio:.2f}x baseline; score {score:.2f} >= {self.minimum_score:.2f}"
        )
        if "forecasting" in extra:
            explanation += "; ARIMA/SARIMA agree"
        return done(
            ACCEPTED,
            reason,
            side=side,
            candidate_side=side,
            score=score,
            regime=regime_info,
            components={k: round(v, 4) for k, v in components.items()},
            signal_bar_time=_time_out(bar_time),
            signal_id=signal_id,
            explanation=explanation,
            **extra,
        )
