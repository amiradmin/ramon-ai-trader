from __future__ import annotations

from ramon.core import Bar, Forecast, Market, Settings, evaluate


class FixedModel:
    def __init__(self, forecast: Forecast) -> None:
        self.value = forecast

    def forecast(self, closes: list[float], horizon: int) -> Forecast:
        assert horizon == 4
        return self.value


def falling_bars() -> tuple[Bar, ...]:
    flat_count = 243
    values = [104.0] * flat_count
    values.extend(104.0 - (4.0 * i / 12.0) for i in range(1, 13))
    values.append(100.0)
    return tuple(
        Bar(
            1_800_000_000 + i * 900,
            close,
            close + 0.5,
            close - 0.5,
            close,
        )
        for i, close in enumerate(values)
    )


def reversal_micro_bars() -> tuple[Bar, ...]:
    values = (99.65, 99.55, 99.75, 100.00)
    return tuple(
        Bar(
            1_800_300_000 + i * 60,
            close,
            close + 0.05,
            close - 0.05,
            close,
        )
        for i, close in enumerate(values)
    )


def test_trend_conflict_still_blocks_unconfirmed_countertrend_forecast() -> None:
    market = Market("XAUUSD_l", "M15", 100.0, 100.4, 0.01, falling_bars())
    model = FixedModel(Forecast(100.5, 103.0, 103.5, (100.5, 101.2, 102.1, 103.0)))

    result = evaluate(market, model)

    assert result.decision == "WAIT"
    assert result.reason == "trend_conflict"
    assert result.signal_strength >= 0.70


def test_trend_conflict_allows_only_confirmed_high_confidence_reversal() -> None:
    market = Market(
        "XAUUSD_l",
        "M15",
        100.0,
        100.4,
        0.01,
        falling_bars(),
        reversal_micro_bars(),
    )
    model = FixedModel(Forecast(100.5, 103.0, 103.5, (100.5, 101.2, 102.1, 103.0)))

    result = evaluate(market, model)

    assert result.signal_strength >= 0.70
    assert result.intrabar_confirmed == 1
    assert result.ai_trend_confirmed == 1
    assert result.ai_trend_direction == "BUY"
    assert result.decision == "BUY"
    assert result.reason == "forecast_up"


def test_trend_conflict_does_not_override_for_medium_confidence_reversal() -> None:
    market = Market(
        "XAUUSD_l",
        "M15",
        100.0,
        100.4,
        0.01,
        falling_bars(),
        reversal_micro_bars(),
    )
    model = FixedModel(Forecast(95.0, 103.0, 105.0, (100.5, 101.2, 102.1, 103.0)))

    result = evaluate(market, model)

    assert 0.20 <= result.signal_strength < 0.70
    assert result.intrabar_confirmed == 1
    assert result.ai_trend_confirmed == 1
    assert result.decision == "WAIT"
    assert result.reason == "trend_conflict"
