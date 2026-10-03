from __future__ import annotations

from ramon.core import Bar, Forecast
from ramon.eliot_research import Row
from ramon.eliot_forward import candidate, forward, samples


class UpModel:
    def forecast(self, closes, horizon):
        return Forecast(closes[-1], closes[-1] + 2, closes[-1] + 4,
                        (closes[-1] + 1, closes[-1] + 1.5, closes[-1] + 2))


class FixedRole:
    def __init__(self, probability):
        self.probability = probability

    def predict_proba(self, values):
        return self.probability


def rows(count=100):
    return [Row(Bar((i + 1) * 300, 100, 100.5, 99.5, 100), .42)
            for i in range(count)]


def test_chronos_candidate_features_use_no_next_bar():
    history = rows()
    before = candidate(history, 70, UpModel())
    history[71] = Row(Bar(72 * 300, 300, 400, 200, 300), .42)
    assert candidate(history, 70, UpModel()) == before


def test_frozen_cutoff_excludes_all_old_bars_and_waits_for_new_data():
    history = rows()
    cutoff = history[80].bar.time
    roles = {"profit": FixedRole(.8), "take": FixedRole(.8), "stop": FixedRole(.1)}
    assert forward(history[:81], None, roles, cutoff=cutoff,
                   units_per_price=1)["status"] == "AWAITING_NEW_M5_BARS"
    result = forward(history, UpModel(), roles, cutoff=cutoff, units_per_price=1)
    expected = list(samples(history, UpModel(), start=81, end=100, units_per_price=1))
    assert result["candidate_signals"] == len(expected)
    assert result["new_completed_bars"] == 19
    assert all(index > 80 for index, _, _, _ in expected)
