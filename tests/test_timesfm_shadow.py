from dataclasses import replace

import pytest

from ramon.timesfm_experimental import TimesFM3Experimental
from test_ensemble import _decision, _market


class Output:
    forecast = [101.0, 102.0, 103.0, 104.0]
    quantiles = [
        [99.0, 99.2, 99.4, 99.6, 101.0, 101.2, 101.4, 101.6, 102.0],
        [100.0, 100.2, 100.4, 100.6, 102.0, 102.2, 102.4, 102.6, 103.0],
        [101.0, 101.2, 101.4, 101.6, 103.0, 103.2, 103.4, 103.6, 104.0],
        [102.0, 102.2, 102.4, 102.6, 104.0, 104.2, 104.4, 104.6, 105.0],
    ]


class FakeForecaster:
    def predict(self, **kwargs):
        return Output()


class BrokenForecaster:
    def predict(self, **kwargs):
        raise RuntimeError("boom")


def test_disabled_timesfm_experimental_is_unavailable_and_harmless():
    shadow = TimesFM3Experimental(enabled=False)
    payload = shadow.assess(_market(), _decision())
    assert payload["timesfm3_experimental_ready"] == 0
    assert payload["timesfm3_experimental_direction"] == "UNAVAILABLE"
    assert payload["timesfm3_experimental_effect"] == "NONE"


def test_ready_timesfm_experimental_reports_forecast_only():
    shadow = TimesFM3Experimental(enabled=True, forecaster=FakeForecaster())
    decision = replace(_decision(), decision="SELL")
    payload = shadow.assess(_market(), decision)
    assert payload["timesfm3_experimental_ready"] == 1
    assert payload["timesfm3_experimental_direction"] == "BUY"
    assert payload["timesfm3_experimental_median"] == pytest.approx(104.0)
    assert payload["timesfm3_experimental_low"] == pytest.approx(102.0)
    assert payload["timesfm3_experimental_high"] == pytest.approx(105.0)
    assert payload["timesfm3_experimental_effect"] == "NONE"


def test_timesfm_failure_stays_display_only():
    shadow = TimesFM3Experimental(enabled=True, forecaster=BrokenForecaster())
    payload = shadow.assess(_market(), _decision())
    assert payload["timesfm3_experimental_ready"] == 0
    assert payload["timesfm3_experimental_direction"] == "UNAVAILABLE"
    assert "RuntimeError" in payload["timesfm3_experimental_error"]
    assert payload["timesfm3_experimental_effect"] == "NONE"
