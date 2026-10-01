import json
import sqlite3

from ramon.entry_timing_lab import (
    TimingRow,
    _chronos_direction,
    as_sample,
    bootstrap_difference_ci,
    direction_accuracy,
    split_holdout,
)


def row(i: int, *, selected: bool, move: float = 0.5) -> TimingRow:
    return TimingRow(
        signal_bar_time=i * 900,
        captured=i * 900 + 10,
        quote_time=i * 900 + 901,
        direction="BUY",
        mid=100.0,
        spread=0.4,
        atr=2.0,
        stop_distance=3.0,
        target_distance=6.0,
        point=0.01,
        selected=selected,
        future_move_atr=move,
    )


def test_chronos_direction_uses_dominant_stored_edge():
    buy = json.dumps({
        "decision_audit": {"base": {"buy_edge": 2.0, "sell_edge": -1.0}}
    })
    sell = json.dumps({
        "decision_audit": {"base": {"buy_edge": -0.5, "sell_edge": 0.2}}
    })
    tie = json.dumps({
        "decision_audit": {"base": {"buy_edge": 0.0, "sell_edge": 0.0}}
    })
    assert _chronos_direction(buy) == "BUY"
    assert _chronos_direction(sell) == "SELL"
    assert _chronos_direction(tie) == "BUY"
    assert _chronos_direction(None) is None


def test_split_holdout_is_chronological():
    rows = [row(i, selected=i % 2 == 0) for i in range(10, 0, -1)]
    development, test = split_holdout(rows, 0.30)
    assert len(development) == 7
    assert len(test) == 3
    assert development[-1].signal_bar_time < test[0].signal_bar_time


def test_direction_accuracy_uses_signed_future_move():
    rows = [
        row(1, selected=True, move=1.0),
        row(2, selected=True, move=-1.0),
        row(3, selected=False, move=2.0),
        row(4, selected=False, move=0.0),
    ]
    assert direction_accuracy(rows) == 200.0 / 3.0


def test_bootstrap_difference_is_deterministic():
    selected = [1.0, 2.0, 3.0, 4.0]
    nonselected = [-1.0, 0.0, 1.0, 2.0]
    one = bootstrap_difference_ci(
        selected, nonselected, iterations=500, seed=7
    )
    two = bootstrap_difference_ci(
        selected, nonselected, iterations=500, seed=7
    )
    assert one == two
    mean, low, high = one
    assert mean == 1.5
    assert low <= mean <= high


def test_as_sample_preserves_bar_level_replay_geometry():
    timing = row(5, selected=True)
    sample = as_sample(timing)
    assert sample.direction == timing.direction
    assert sample.entry_time == timing.quote_time
    assert sample.mid == timing.mid
    assert sample.spread == timing.spread
    assert sample.stop_distance == timing.stop_distance
    assert sample.target_distance == timing.target_distance
    assert sample.point == timing.point
