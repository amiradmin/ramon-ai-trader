import pytest

from ramon.core import Bar, Forecast
from ramon.model_tournament import tournament


class Fixed:
    def forecast(self, closes, horizon):
        p = closes[-1]
        return Forecast(p-1, p+3, p+4, tuple(p+(j+1)*.75 for j in range(horizon)))


def history():
    return [Bar(1800000000+i*900,100+i*.15,100+i*.15+.4,100+i*.15-.4,100+i*.15+.05)
            for i in range(380)]


def test_identical_models_use_identical_window_costs_without_promotion():
    bars = history()
    result = tournament(bars,[10]*len(bars),{"a":Fixed(),"b":Fixed()},start=256,point=.01,cost_r=.05)
    assert result["results"]["a"] == result["results"]["b"]
    assert not result["promotion"]
    assert result["holdout_start_mt5"] == bars[256].time


def test_missing_spreads_and_discontinuous_forecast_horizon_fail_closed():
    bars = history()
    with pytest.raises(ValueError,match="missing recorded spreads"):
        tournament(bars,[0]*len(bars),{"a":Fixed(),"b":Fixed()},start=256,point=.01,cost_r=.05)
    bars[300] = Bar(bars[300].time+30, bars[300].open,bars[300].high,bars[300].low,bars[300].close)
    with pytest.raises(ValueError,match="contiguous"):
        tournament(bars,[10]*len(bars),{"a":Fixed(),"b":Fixed()},start=256,point=.01,cost_r=.05)
