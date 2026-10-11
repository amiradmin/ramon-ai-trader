import math
import pytest

from ramon.trade_audit import audit, summarize, session_name


def test_profit_factor_and_drawdown():
    result = summarize([2.0, -1.0, -2.0, 4.0])
    assert result["net"] == 3.0
    assert result["profit_factor"] == 2.0
    assert result["max_drawdown"] == 3.0
    assert result["win_rate"] == 0.5


def test_zero_losses_do_not_fake_infinite_profit_factor():
    assert summarize([1.0, 2.0])["profit_factor"] is None
    assert summarize([])["win_rate"] is None


def test_rejects_gross_only_or_nonfinite_or_naive_timestamps():
    for bad in (
        [{"closed_utc": "2026-10-01T10:00:00Z", "profit_units": "10"}],
        [{"closed_utc": "2026-10-01T10:00:00Z", "net_units": "nan"}],
        [{"closed_utc": "2026-10-01T10:00:00", "net_units": "3"}],
    ):
        with pytest.raises((ValueError, KeyError)):
            audit(bad)


def test_chronological_drawdown_and_grouping():
    rows = [
        {"closed_utc": "2026-10-01T12:00:00Z", "net_units": "-3", "regime": "RANGE"},
        {"closed_utc": "2026-10-01T10:00:00Z", "net_units": "2", "regime": "RANGE"},
    ]
    result = audit(rows)
    assert result["overall"]["max_drawdown"] == 3.0
    assert result["by"]["regime"]["RANGE"]["trades"] == 2
    assert session_name("2026-10-01T10:00:00+00:00") == "UTC_07_14"
