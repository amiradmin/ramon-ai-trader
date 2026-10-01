import json

from ramon.ablation_lab import (
    _chronos_direction_from_metadata,
    ma_trend_direction,
    matched_ratio_random_direction,
    n_bar_momentum_direction,
    previous_bar_reversal_direction,
    replay_with_cost_stress,
)
from ramon.validation_lab import Sample


def sample(**changes):
    base = dict(
        captured=1000,
        signal_bar_time=900,
        entry_time=1000,
        entry_time_utc=1000,
        direction="BUY",
        mid=100.0,
        spread=0.4,
        stop_distance=2.0,
        target_distance=4.0,
        point=0.01,
    )
    base.update(changes)
    return Sample(**base)


def bars():
    rows = []
    close = 100.0
    for i in range(20):
        op = close
        close = 100.0 + i * 0.5
        rows.append(
            {
                "time": i * 900,
                "open": op,
                "high": max(op, close) + 0.2,
                "low": min(op, close) - 0.2,
                "close": close,
                "spread_points": 40,
            }
        )
    return rows


def test_chronos_only_uses_stored_base_edges():
    raw = json.dumps(
        {
            "decision_audit": {
                "base": {
                    "buy_edge": 2.5,
                    "sell_edge": -3.0,
                }
            }
        }
    )
    assert _chronos_direction_from_metadata(raw) == "BUY"
    raw = json.dumps(
        {
            "decision_audit": {
                "base": {
                    "buy_edge": -2.5,
                    "sell_edge": 3.0,
                }
            }
        }
    )
    assert _chronos_direction_from_metadata(raw) == "SELL"
    assert _chronos_direction_from_metadata(None) is None


def test_price_only_baselines_use_completed_bars_before_entry():
    data = bars()
    s = sample(entry_time=20 * 900 + 100)
    assert n_bar_momentum_direction(data, s, lookback=4) == "BUY"
    assert ma_trend_direction(data, s, fast=4, slow=12) == "BUY"
    assert previous_bar_reversal_direction(data, s) == "SELL"


def test_matched_ratio_random_is_deterministic():
    s = sample()
    one = matched_ratio_random_direction(s, 42, 0.70)
    two = matched_ratio_random_direction(s, 42, 0.70)
    assert one == two
    assert one in {"BUY", "SELL"}
    assert matched_ratio_random_direction(s, 42, 1.0) == "BUY"
    assert matched_ratio_random_direction(s, 42, 0.0) == "SELL"


def test_cost_stress_worsens_sell_ask_side_when_spread_is_larger():
    s = sample(
        direction="SELL",
        mid=100.2,
        spread=0.4,
        stop_distance=1.0,
        target_distance=1.0,
        entry_time=1000,
    )
    data = [
        {"time": 900, "open": 100.0, "high": 100.2, "low": 99.8, "close": 100.0, "spread_points": 40},
        {"time": 1800, "open": 100.0, "high": 100.45, "low": 98.8, "close": 99.1, "spread_points": 40},
    ]
    normal = replay_with_cost_stress(data, s, "SELL", max_bars=1, cost_mult=1.0)
    stressed = replay_with_cost_stress(data, s, "SELL", max_bars=1, cost_mult=2.0)
    assert normal is not None and stressed is not None
    assert normal.exit_kind == "TP"
    assert stressed.exit_kind == "SL"
