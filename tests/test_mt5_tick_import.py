import pytest

from ramon.mt5_tick_import import validate_export


def tick(t, bid="4200.00", ask="4200.42"):
    return {"time_ms": str(t), "bid": bid, "ask": ask}


def test_canonical_quotes_and_gap_statistics():
    output, report = validate_export(
        [tick(1000), tick(1000), tick(2000), tick(64000)],
        expected_symbol="XAUUSD_l", utc_clock_confirmed=True)
    assert len(output) == 3
    assert report["exact_duplicates_removed"] == 1
    assert report["largest_gap_ms"] == 62000
    assert report["gaps_gt_60s"] == 1
    assert report["min_spread_price"] == 0.42


def test_operator_clock_gate_is_required():
    with pytest.raises(ValueError, match="clock"):
        validate_export([tick(1000)], expected_symbol="XAUUSD_l")


@pytest.mark.parametrize("rows", [
    [],
    [tick(2000), tick(1000)],
    [tick(1000), tick(1000, ask="4200.50")],
    [tick(1000, bid="4201", ask="4200")],
    [tick(1000, bid="nan")],
    [tick(0)],
])
def test_unsafe_tick_archives_are_rejected(rows):
    with pytest.raises(ValueError):
        validate_export(rows, expected_symbol="XAUUSD_l", utc_clock_confirmed=True)
