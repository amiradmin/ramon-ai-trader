import numpy as np
import pytest

from ramon.timesfm_shadow import TimesFMShadow


class FakeBackend:
    def forecast(self, *, horizon, inputs):
        assert len(inputs) == 1
        assert len(inputs[0]) == 16
        assert horizon == 4
        points = np.array([[101., 102., 103., 104.]])
        quantiles = np.zeros((1, 4, 10))
        quantiles[0, -1, [1, 5, 9]] = [102., 104., 106.]
        return points, quantiles


def test_shadow_adapter_uses_only_past_context_and_horizon_quantiles():
    model = TimesFMShadow(context=16, backend=FakeBackend())
    result = model.forecast([100.0] * 16, 4)
    assert (result.low, result.median, result.high) == (102., 104., 106.)
    assert result.median_path == (101., 102., 103., 104.)


def test_shadow_rejects_invalid_input():
    model = TimesFMShadow(context=16, backend=FakeBackend())
    with pytest.raises(ValueError, match="Ramon 4-bar horizon"):
        model.forecast([100.] * 16, 8)
