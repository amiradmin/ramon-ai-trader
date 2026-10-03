from __future__ import annotations

from ramon.core import Bar, Forecast
from ramon.eliot_research import Row
from ramon.eliot_roles import eligible, evaluate, features, outcome


def sample_rows(count=90):
    return [Row(Bar(i * 300 + 300, 100, 100.2, 99.8, 100), .42)
            for i in range(count)]


def test_entry_bar_fills_at_ask_and_ambiguous_touch_stops_first():
    rows = sample_rows()
    rows[64] = Row(Bar(65 * 300, 100, 106, 94, 100), .42)
    assert eligible(rows, 63)
    assert outcome(rows, 63, 1, 1) == ("SL", -6)


def test_features_use_only_current_and_earlier_bars():
    rows = sample_rows()
    before = features(rows, 64, 1)
    rows[65] = Row(Bar(66 * 300, 1, 200, 1, 200), 2)
    assert features(rows, 64, 1) == before


class FlatChronos:
    def forecast(self, closes, horizon):
        return Forecast(98, 102, 106)


class FixedRole:
    def __init__(self, probability):
        self.probability = probability

    def predict_proba(self, features):
        return self.probability


def test_role_gate_filters_without_adding_baseline_entries():
    rows = sample_rows(100)
    models = {"regime": FixedRole(.9), "direction": FixedRole(.9),
              "entry": FixedRole(.1)}
    result = evaluate(rows, FlatChronos(), models, start=63, end=90,
                      units_per_price=1)
    assert result["chronos_only"]["entries"] > 0
    assert result["chronos_with_m5_roles"]["entries"] == 0
