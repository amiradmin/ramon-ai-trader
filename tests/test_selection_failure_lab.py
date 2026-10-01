import json

from ramon.selection_failure_lab import (
    FailureRow,
    _flatten_numeric,
    feature_stats,
    selected_outcome_feature_stats,
)
from ramon.validation_lab import Result


def make_row(i: int, selected: bool, features: dict[str, float]) -> FailureRow:
    return FailureRow(
        signal_bar_time=i * 900,
        captured=i * 900 + 1,
        quote_time=i * 900 + 901,
        selected=selected,
        direction="BUY",
        mid=100.0,
        spread=0.4,
        atr=2.0,
        stop_distance=3.0,
        target_distance=6.0,
        point=0.01,
        features=features,
    )


def test_flatten_numeric_skips_nested_settings_and_keeps_numbers():
    out = {}
    _flatten_numeric(
        "root",
        {
            "a": 1.5,
            "b": {"c": 2, "settings": {"ignored": 99}},
            "flag": True,
            "text": "x",
        },
        out,
    )
    assert out["root.a"] == 1.5
    assert out["root.b.c"] == 2.0
    assert out["root.flag"] == 1.0
    assert "root.b.settings.ignored" not in out
    assert "root.text" not in out


def test_feature_stats_orders_largest_standardized_shift_first():
    rows = [
        make_row(1, True, {"big": 10.0, "small": 1.0}),
        make_row(2, True, {"big": 11.0, "small": 1.1}),
        make_row(3, False, {"big": 1.0, "small": 0.9}),
        make_row(4, False, {"big": 2.0, "small": 1.0}),
    ]
    stats = feature_stats(rows, min_coverage=1.0)
    assert stats[0][0] == "big"
    assert stats[0][-1] > 0


def test_selected_outcome_feature_stats_detects_negative_relation():
    rows = [
        make_row(i, True, {"strength": float(i)})
        for i in range(1, 13)
    ]
    outcomes = {
        row.signal_bar_time: Result(
            direction="BUY",
            outcome_r=-float(i),
            bars_held=1,
            exit_kind="TIME",
        )
        for i, row in enumerate(rows, 1)
    }
    stats = selected_outcome_feature_stats(rows, outcomes, min_count=10)
    assert stats
    name, n, corr, low_r, high_r = stats[0]
    assert name == "strength"
    assert n == 12
    assert corr < -0.99
    assert high_r < low_r
