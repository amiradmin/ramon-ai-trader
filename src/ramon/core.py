from __future__ import annotations

from dataclasses import asdict, dataclass
from math import isfinite
from typing import Protocol, Sequence


@dataclass(frozen=True, slots=True)
class Bar:
    time: int
    open: float
    high: float
    low: float
    close: float


@dataclass(frozen=True, slots=True)
class Market:
    symbol: str
    timeframe: str
    bid: float
    ask: float
    point: float
    bars: tuple[Bar, ...]
    micro_bars: tuple[Bar, ...] = ()

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> Market:
        raw = payload.get("bars")
        micro_raw = payload.get("micro_bars", [])
        if not isinstance(raw, list):
            raise ValueError("bars must be a list")
        if not isinstance(micro_raw, list):
            raise ValueError("micro_bars must be a list")
        try:
            bars = tuple(
                Bar(
                    time=int(row["time"]),
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                )
                for row in raw
            )
            micro_bars = tuple(
                Bar(
                    time=int(row["time"]),
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                )
                for row in micro_raw
            )
            market = cls(
                symbol=str(payload["symbol"]),
                timeframe=str(payload["timeframe"]),
                bid=float(payload["bid"]),
                ask=float(payload["ask"]),
                point=float(payload["point"]),
                bars=bars,
                micro_bars=micro_bars,
            )
        except (TypeError, KeyError, ValueError, OverflowError) as exc:
            raise ValueError("invalid market payload") from exc
        market.validate()
        return market

    @staticmethod
    def _validate_bar_sequence(
        values: tuple[Bar, ...],
        *,
        label: str,
        completed: bool,
    ) -> None:
        previous = 0
        for bar in values:
            if bar.time <= previous or bar.time <= 0:
                suffix = " completed candles" if completed else " bars"
                raise ValueError(f"{label} must be strictly increasing{suffix}")
            previous = bar.time
            if not all(isfinite(x) and x > 0 for x in (bar.open, bar.high, bar.low, bar.close)):
                raise ValueError(f"non-finite or nonpositive {label}")
            if bar.low > min(bar.open, bar.close) or bar.high < max(bar.open, bar.close):
                raise ValueError(f"inconsistent {label} OHLC")

    def validate(self) -> None:
        if not self.symbol.upper().startswith("XAUUSD") or self.timeframe != "M15":
            raise ValueError("only XAUUSD variants on M15 are supported")
        if len(self.bars) < 128 or len(self.bars) > 1024:
            raise ValueError("need 128..1024 completed bars")
        if self.micro_bars and not 3 <= len(self.micro_bars) <= 8:
            raise ValueError("need 3..8 micro bars when supplied")
        if not all(isfinite(x) for x in (self.bid, self.ask, self.point)):
            raise ValueError("non-finite market prices")
        if not (self.ask > self.bid > 0 and self.point > 0):
            raise ValueError("invalid bid/ask/point")
        self._validate_bar_sequence(self.bars, label="bars", completed=True)
        self._validate_bar_sequence(self.micro_bars, label="micro_bars", completed=False)


@dataclass(frozen=True, slots=True)
class Settings:
    horizon: int = 4
    context: int = 256
    max_spread_points: int = 50
    minimum_edge_atr: float = 0.12
    minimum_edge_spreads: float = 1.5
    minimum_strength: float = 0.20
    intrabar_min_strength: float = 0.05
    intrabar_min_move_atr: float = 0.06
    intrabar_min_rebound_atr: float = 0.08
    stop_atr: float = 1.5
    target_atr: float = 3.0


@dataclass(frozen=True, slots=True)
class Forecast:
    low: float
    median: float
    high: float


class Forecaster(Protocol):
    def forecast(self, closes: Sequence[float], horizon: int) -> Forecast: ...


@dataclass(frozen=True, slots=True)
class Decision:
    decision: str
    reason: str
    signal_bar_time: int
    signal_bid: float
    signal_ask: float
    spread_points: int
    atr: float
    edge: float
    buy_edge: float
    sell_edge: float
    minimum_edge: float
    uncertainty: float
    signal_strength: float
    minimum_strength: float
    intrabar_confirmed: int
    intrabar_direction: str
    intrabar_move_atr: float
    intrabar_rebound_atr: float
    intrabar_min_strength: float
    intrabar_min_move_atr: float
    intrabar_min_rebound_atr: float
    forecast_low: float
    forecast_median: float
    forecast_high: float
    stop_distance: float
    target_distance: float

    def to_dict(self) -> dict[str, str | int | float]:
        return asdict(self)


def atr14(bars: Sequence[Bar]) -> float:
    """Mean of the last 14 true ranges, using only completed history."""
    if len(bars) < 15:
        raise ValueError("15 bars required for ATR")
    ranges = [
        max(
            bar.high - bar.low,
            abs(bar.high - previous.close),
            abs(bar.low - previous.close),
        )
        for previous, bar in zip(bars[-15:-1], bars[-14:])
    ]
    return sum(ranges) / len(ranges)


def _intrabar_metrics(
    market: Market,
    *,
    atr: float,
    direction: str,
) -> tuple[float, float, bool]:
    """Measure 3-4 minute momentum and rebound from a recent intrabar extreme."""
    if len(market.micro_bars) < 3 or atr <= 0:
        return 0.0, 0.0, False

    micro = market.micro_bars
    anchor = micro[0].close
    previous_close = micro[-2].close
    recent_start = max(0, len(micro) - 3)

    if direction == "BUY":
        low_index = min(range(len(micro)), key=lambda index: micro[index].low)
        move_atr = (market.bid - anchor) / atr
        rebound_atr = (market.bid - micro[low_index].low) / atr
        turn = market.bid > previous_close and low_index >= recent_start
    else:
        high_index = max(range(len(micro)), key=lambda index: micro[index].high)
        move_atr = (anchor - market.bid) / atr
        rebound_atr = (micro[high_index].high - market.bid) / atr
        turn = market.bid < previous_close and high_index >= recent_start

    return move_atr, rebound_atr, turn


def evaluate(market: Market, forecaster: Forecaster, settings: Settings = Settings()) -> Decision:
    """Make a model-led BUY/SELL/WAIT decision; safety checks stay outside the model."""
    market.validate()
    if settings.horizon < 1 or settings.context < 128:
        raise ValueError("invalid horizon/context")
    if not (
        0 <= settings.intrabar_min_strength <= settings.minimum_strength
        and settings.intrabar_min_move_atr >= 0
        and settings.intrabar_min_rebound_atr >= 0
    ):
        raise ValueError("invalid intrabar settings")

    atr = atr14(market.bars)
    spread = market.ask - market.bid
    spread_points = round(spread / market.point)
    reason = ""
    forecast = Forecast(0.0, 0.0, 0.0)
    edge = 0.0
    buy_edge = 0.0
    sell_edge = 0.0
    minimum = max(settings.minimum_edge_atr * atr, settings.minimum_edge_spreads * spread)
    uncertainty = 0.0
    signal_strength = 0.0
    side = "WAIT"
    intrabar_confirmed = 0
    intrabar_direction = "NONE"
    intrabar_move_atr = 0.0
    intrabar_rebound_atr = 0.0

    if atr <= market.point or spread_points > settings.max_spread_points:
        reason = "spread_or_atr"
    else:
        forecast = forecaster.forecast(
            [bar.close for bar in market.bars[-settings.context :]], settings.horizon
        )
        if (
            not all(isfinite(v) and v > 0 for v in (forecast.low, forecast.median, forecast.high))
            or not forecast.low <= forecast.median <= forecast.high
        ):
            raise ValueError("invalid model forecast")

        # Bars and micro-bars are broker Bid prices: BUY pays Ask; SELL later pays Ask.
        buy_edge = forecast.median - market.ask
        sell_edge = market.bid - (forecast.median + spread)
        uncertainty = max(forecast.high - forecast.low, market.point)
        buy_strength = buy_edge / uncertainty
        sell_strength = sell_edge / uncertainty
        signal_strength = max(buy_strength, sell_strength)
        dominant_buy = buy_edge > sell_edge
        dominant_edge = buy_edge if dominant_buy else sell_edge
        dominant_strength = buy_strength if dominant_buy else sell_strength
        intrabar_direction = "BUY" if dominant_buy else "SELL"

        intrabar_move_atr, intrabar_rebound_atr, micro_turn = _intrabar_metrics(
            market,
            atr=atr,
            direction=intrabar_direction,
        )
        if (
            dominant_edge >= minimum
            and dominant_strength >= settings.intrabar_min_strength
            and intrabar_move_atr >= settings.intrabar_min_move_atr
            and intrabar_rebound_atr >= settings.intrabar_min_rebound_atr
            and micro_turn
        ):
            intrabar_confirmed = 1

        if dominant_buy and buy_edge >= minimum and buy_strength >= settings.minimum_strength:
            side, edge, reason = "BUY", buy_edge, "forecast_up"
        elif not dominant_buy and sell_edge >= minimum and sell_strength >= settings.minimum_strength:
            side, edge, reason = "SELL", sell_edge, "forecast_down"
        elif dominant_buy and intrabar_confirmed:
            side, edge, reason = "BUY", buy_edge, "intrabar_reversal_up"
        elif not dominant_buy and intrabar_confirmed:
            side, edge, reason = "SELL", sell_edge, "intrabar_reversal_down"
        else:
            if dominant_edge < minimum:
                reason = "insufficient_model_edge"
            elif signal_strength < settings.minimum_strength:
                reason = "insufficient_model_strength"
            else:
                reason = "insufficient_model_edge"

    return Decision(
        decision=side,
        reason=reason,
        signal_bar_time=market.bars[-1].time,
        signal_bid=market.bid,
        signal_ask=market.ask,
        spread_points=spread_points,
        atr=atr,
        edge=edge,
        buy_edge=buy_edge,
        sell_edge=sell_edge,
        minimum_edge=minimum,
        uncertainty=uncertainty,
        signal_strength=signal_strength,
        minimum_strength=settings.minimum_strength,
        intrabar_confirmed=intrabar_confirmed,
        intrabar_direction=intrabar_direction,
        intrabar_move_atr=intrabar_move_atr,
        intrabar_rebound_atr=intrabar_rebound_atr,
        intrabar_min_strength=settings.intrabar_min_strength,
        intrabar_min_move_atr=settings.intrabar_min_move_atr,
        intrabar_min_rebound_atr=settings.intrabar_min_rebound_atr,
        forecast_low=forecast.low,
        forecast_median=forecast.median,
        forecast_high=forecast.high,
        stop_distance=settings.stop_atr * atr,
        target_distance=settings.target_atr * atr,
    )
