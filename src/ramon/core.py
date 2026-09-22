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

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> Market:
        raw = payload.get("bars")
        if not isinstance(raw, list):
            raise ValueError("bars must be a list")
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
            market = cls(
                symbol=str(payload["symbol"]),
                timeframe=str(payload["timeframe"]),
                bid=float(payload["bid"]),
                ask=float(payload["ask"]),
                point=float(payload["point"]),
                bars=bars,
            )
        except (TypeError, KeyError, ValueError, OverflowError) as exc:
            raise ValueError("invalid market payload") from exc
        market.validate()
        return market

    def validate(self) -> None:
        if not self.symbol.upper().startswith("XAUUSD") or self.timeframe != "M15":
            raise ValueError("only XAUUSD variants on M15 are supported")
        if len(self.bars) < 128 or len(self.bars) > 1024:
            raise ValueError("need 128..1024 completed bars")
        if not all(isfinite(x) for x in (self.bid, self.ask, self.point)):
            raise ValueError("non-finite market prices")
        if not (self.ask > self.bid > 0 and self.point > 0):
            raise ValueError("invalid bid/ask/point")
        previous = 0
        for bar in self.bars:
            if bar.time <= previous or bar.time <= 0:
                raise ValueError("bars must be strictly increasing completed candles")
            previous = bar.time
            if not all(isfinite(x) and x > 0 for x in (bar.open, bar.high, bar.low, bar.close)):
                raise ValueError("non-finite or nonpositive OHLC")
            if bar.low > min(bar.open, bar.close) or bar.high < max(bar.open, bar.close):
                raise ValueError("inconsistent OHLC")


@dataclass(frozen=True, slots=True)
class Settings:
    horizon: int = 4
    context: int = 256
    max_spread_points: int = 50
    minimum_edge_atr: float = 0.12
    minimum_edge_spreads: float = 1.5
    minimum_strength: float = 0.20
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
    spread_points: int
    atr: float
    edge: float
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


def evaluate(market: Market, forecaster: Forecaster, settings: Settings = Settings()) -> Decision:
    """Make a model-led BUY/SELL/WAIT decision; safety checks stay outside the model."""
    market.validate()
    if settings.horizon < 1 or settings.context < 128:
        raise ValueError("invalid horizon/context")
    atr = atr14(market.bars)
    spread = market.ask - market.bid
    spread_points = round(spread / market.point)
    reason = ""
    forecast = Forecast(0.0, 0.0, 0.0)
    edge = 0.0
    side = "WAIT"

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
        # Bars are broker Bid prices: BUY pays Ask; SELL later pays Ask.
        buy_edge = forecast.median - market.ask
        sell_edge = market.bid - (forecast.median + spread)
        minimum = max(settings.minimum_edge_atr * atr, settings.minimum_edge_spreads * spread)
        uncertainty = max(forecast.high - forecast.low, market.point)
        if buy_edge > sell_edge and buy_edge >= minimum and buy_edge / uncertainty >= settings.minimum_strength:
            side, edge, reason = "BUY", buy_edge, "forecast_up"
        elif sell_edge > buy_edge and sell_edge >= minimum and sell_edge / uncertainty >= settings.minimum_strength:
            side, edge, reason = "SELL", sell_edge, "forecast_down"
        else:
            reason = "insufficient_model_edge"

    return Decision(
        decision=side,
        reason=reason,
        signal_bar_time=market.bars[-1].time,
        spread_points=spread_points,
        atr=atr,
        edge=edge,
        forecast_low=forecast.low,
        forecast_median=forecast.median,
        forecast_high=forecast.high,
        stop_distance=settings.stop_atr * atr,
        target_distance=settings.target_atr * atr,
    )
