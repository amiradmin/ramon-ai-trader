from __future__ import annotations

from ramon.core import Bar, Forecast
from ramon.eliot_research import Row, replay


class UpForecast:
    def forecast(self, closes, horizon):
        return Forecast(closes[-1] + 9, closes[-1] + 9, closes[-1] + 9)


class WeakForecast:
    def forecast(self, closes, horizon):
        return Forecast(closes[-1], closes[-1] + 2, closes[-1] + 3)


def test_wait_reports_forecast_move_and_required_move():
    rows = [Row(Bar(i * 300 + 300, 100, 100, 100, 100), .42)
            for i in range(264)]
    result = replay(rows, WeakForecast(), start=256, max_decisions=1)
    assert result["outcomes"] == {"WAIT": 1}
    assert result["forecast_abs_move_price_p50"] == 2
    assert result["entry_required_move_price_p50"] == 5.42
    assert result["forecast_abs_move_price_max"] == 2


def test_ambiguous_entry_bar_counts_stop_first_and_uses_ask_entry():
    rows = [Row(Bar(i * 300 + 300, 100, 100, 100, 100), .42)
            for i in range(264)]
    # BUY at next open ask=100.42; TP=105.42 and SL=94.42 both
    # fall inside the entry bar. A one-bar replay must count the loss.
    rows[257] = Row(Bar(258 * 300, 100, 106, 94, 100), .42)
    result = replay(rows, UpForecast(), start=256, max_decisions=1)
    assert result["entries"] == 1
    assert result["outcomes"] == {"SL": 1}
    assert result["net_account_units"] == -6


def test_gap_in_context_skips_forecast_and_trade():
    rows = [Row(Bar(i * 300 + 300, 100, 100, 100, 100), .42)
            for i in range(264)]
    rows[255] = Row(Bar(255 * 300 + 3600, 100, 100, 100, 100), .42)
    result = replay(rows, UpForecast(), start=256, max_decisions=1)
    assert result["model_decisions"] == 0
    assert result["entries"] == 0
    assert result["skipped_bars"]["context_gap"] > 0


def test_shorter_contiguous_context_allows_more_eligible_bars():
    rows = [Row(Bar(i * 300 + 300, 100, 100, 100, 100), .42)
            for i in range(300)]
    rows[70] = Row(Bar(71 * 300 + 3600, 100, 100, 100, 100), .42)
    rows[71] = Row(Bar(72 * 300 + 3600, 100, 100, 100, 100), .42)
    # A break at bar 70 is outside a 64-bar context at index 256,
    # but remains inside a 256-bar context at the same index.
    short = replay(rows, WeakForecast(), start=256, max_decisions=1, context_bars=64)
    long = replay(rows, WeakForecast(), start=256, max_decisions=1)
    assert short["model_decisions"] == 1
    assert short["context_bars"] == 64
    assert long["model_decisions"] == 0
