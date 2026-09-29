from datetime import datetime, timezone
import pytest

from ramon.addon_research import Trade, read_trades, test_addon as study
from ramon.stop_feasibility import Bar


def bars_and_trade():
    bars = []
    for i in range(75):
        opened = 100 + 0.2 * i
        bars.append(Bar(1_800_000_000 + i * 300,
                        opened, opened + 0.35, opened - 0.15,
                        opened + 0.2, 10))
    trade = Trade(1, "BUY", bars[44].time, bars[56].time + 300, 4, 10)
    return bars, trade


def test_report_parser_handles_padded_win_and_buy_and_only_exact_utc(tmp_path):
    report = tmp_path / "report.txt"
    report.write_text(
        "001 | WIN  | BUY  | 2026-09-24 00:03 UTC -> 2026-09-24 01:00 UTC "
        "| net=   +7.08 | risk=   7.11 | R=+0.996\n"
        "002 | LOSS | SELL | 2026-09-24 01:00 BROKER[UTC offset unknown] -> "
        "2026-09-24 02:00 UTC | net= -1.00 | risk= 8.00\n"
    )
    rows = read_trades(report, broker_utc_offset=10800)
    assert len(rows) == 1
    assert rows[0].opened_broker == int(datetime(2026, 9, 24, 0, 3,
                                                tzinfo=timezone.utc).timestamp()) + 10800
    with pytest.raises(ValueError, match="offset"):
        read_trades(report, broker_utc_offset=3601)


def test_addon_obeys_combined_risk_and_stop_first_on_ambiguous_bar():
    bars, trade = bars_and_trade()
    # A completed trigger occurs before this bar. Both prices are touched on
    # the candidate entry candle, so the adverse outcome must win the tie.
    decision = study(bars, [trade], usd_per_price=0.01, target_usd=0.003)
    assert decision["addons"] == 1
    entry_index = next(i for i, bar in enumerate(bars)
                       if bar.time == decision["outcomes"][0]["entry_broker"])
    entry_bar = bars[entry_index]
    bars[entry_index] = Bar(entry_bar.time, entry_bar.open,
                            entry_bar.open + 4, entry_bar.open - 4,
                            entry_bar.close, entry_bar.spread_points)
    tied = study(bars, [trade], usd_per_price=0.01, target_usd=0.003)
    assert tied["stop_exits"] == 1
    assert tied["net_usd_proxy"] < 0
    blocked = study(bars, [trade], usd_per_price=0.01, cap_usd=0.10)
    assert blocked["addons"] == 0
    assert blocked["rejected"]["combined_initial_risk_above_cap"] == 1


def test_addon_never_uses_bars_after_main_exit_or_missing_history():
    bars, trade = bars_and_trade()
    early_close = Trade(trade.number, trade.direction, trade.opened_broker,
                        bars[47].time + 120, trade.net_units, trade.risk_units)
    result = study(bars, [early_close], usd_per_price=0.01)
    assert result["addons"] == 0
    later_trade = Trade(trade.number, trade.direction, bars[-1].time + 300,
                        bars[-1].time + 3600, trade.net_units, trade.risk_units)
    result = study(bars, [later_trade], usd_per_price=0.01)
    assert result["rejected"]["outside_history_window"] == 1
