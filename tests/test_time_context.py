from __future__ import annotations

from datetime import datetime, timezone
from math import isclose

from ramon.core import Bar, Forecast
from ramon.model import ChronosForecaster
from ramon.time_context import forecast_clock_inputs, tehran_hour
from ramon.time_context_eval import compare, directional_result, market_regime, path_result, windows


def test_known_future_hours_are_derived_only_from_utc_timestamps() -> None:
    start = int(datetime(2026, 9, 29, 20, 15, tzinfo=timezone.utc).timestamp())
    past, future = forecast_clock_inputs([start - 900, start], horizon=4)
    assert len(past["hour_sin"]) == 2
    assert len(future["hour_sin"]) == 4
    assert isclose(tehran_hour(start), 23.75)
    assert isclose(future["hour_sin"][0], 0.0, abs_tol=1e-10)


def test_chronos_hour_forecast_passes_past_and_future_covariates() -> None:
    class Array:
        @staticmethod
        def asarray(values, dtype):
            return list(values)
        float32 = "float32"

    class Tensor:
        def __getitem__(self, index):
            return self
        def tolist(self):
            return [[99.0, 100.0, 101.0]] * 4

    class Pipeline:
        def predict_quantiles(self, inputs, **kwargs):
            self.inputs = inputs
            assert kwargs["prediction_length"] == 4
            return [Tensor()], None

    model = ChronosForecaster.__new__(ChronosForecaster)
    model._np = Array()
    model.pipeline = Pipeline()
    times = [1_800_000_000 + i * 900 for i in range(128)]

    forecast = model.forecast_with_hour([100.0] * 128, times, 4)

    assert forecast.median == 100.0
    sample = model.pipeline.inputs[0]
    assert set(sample) == {"target", "past_covariates", "future_covariates"}
    assert set(sample["past_covariates"]) == {"hour_sin", "hour_cos"}
    assert len(sample["past_covariates"]["hour_sin"]) == 128
    assert len(sample["future_covariates"]["hour_cos"]) == 4


def test_evaluator_uses_paired_windows_and_excludes_future_gap() -> None:
    bars = tuple(Bar(1_800_000_000 + i * 900, 100, 101, 99, 100)
                 for i in range(140))
    assert windows(bars, context=128, horizon=4, stride=4, max_windows=10)
    gap = list(bars)
    gap[132] = Bar(gap[131].time + 3600, 100, 101, 99, 100)
    assert 131 not in windows(tuple(gap), context=128, horizon=4,
                              stride=4, max_windows=10)

    class FakeModel:
        model_id = "fake"
        def forecast(self, closes, horizon):
            return Forecast(101, 102, 103)
        def forecast_with_hour(self, closes, timestamps, horizon):
            return Forecast(99, 100, 101)

    result = compare(bars, FakeModel(), context=128, max_windows=3, stride=4)
    assert result["all"]["windows"] == 3
    assert result["all"]["hour_mae_atr"] == 0
    assert result["all"]["baseline_mae_atr"] > 0


def test_direction_audit_charges_spread_and_abstains_without_edge() -> None:
    buy, buy_net = directional_result(101, 100, 101, 0.2, 2)
    sell, sell_net = directional_result(99, 100, 99, 0.2, 2)
    assert buy == "BUY" and isclose(buy_net, 0.4)
    assert sell == "SELL" and isclose(sell_net, 0.4)
    assert directional_result(100.1, 100, 101, 0.2, 2) == ("WAIT", 0.0)


def test_first_touch_path_charges_spread_and_resolves_ambiguous_bar_as_loss() -> None:
    bar = Bar(900, 100, 105, 96, 102)
    assert path_result("BUY", 100, (bar,), 0.4, 2) == -1.5
    assert path_result("SELL", 100, (bar,), 0.4, 2) == -1.5
    profitable = Bar(900, 100, 107, 100, 105)
    assert path_result("BUY", 100, (profitable,), 0.4, 2) == 3.0
    assert path_result("WAIT", 100, (profitable,), 0.4, 2) == 0.0


def test_regime_uses_only_completed_history_and_report_has_path_and_buckets() -> None:
    start = 1_800_000_000
    history = tuple(Bar(start + i * 900, 100 + i, 101 + i, 99 + i, 100 + i)
                    for i in range(13))
    assert market_regime(history, 2) == "UP_TREND"

    class FakeModel:
        model_id = "fake"
        def forecast(self, closes, horizon):
            return Forecast(101, 102, 103)
        def forecast_with_hour(self, closes, timestamps, horizon):
            return Forecast(99, 100, 101)

    bars = tuple(Bar(start + i * 900, 100, 101, 99, 100) for i in range(140))
    report = compare(bars, FakeModel(), context=128, max_windows=3,
                     spreads=(0.2,) * len(bars))
    assert report["all"]["realized_best_side_counts"]["WAIT"] == 3
    assert report["all"]["baseline_path_mean_net_atr"] < 0
    assert report["market_regimes"]["RANGE_OR_UNCLEAR"]["windows"] == 3
