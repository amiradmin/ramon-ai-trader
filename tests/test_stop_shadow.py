from ramon.addon_research import Trade
from ramon.stop_feasibility import Bar
from ramon.stop_shadow import examples


def test_completed_pre_entry_bars_only_and_stale_history_rejected():
    bars = [Bar(i * 300, 100 + i, 102 + i, 99 + i, 101 + i, 42)
            for i in range(18)]
    outcomes = {1: {"reason": "DEAL_REASON_SL / stop_loss", "net_units": -9.0},
                2: {"reason": "DEAL_REASON_SL / stop_loss", "net_units": -9.0}}
    trades = [Trade(1, "BUY", 18 * 300 + 60, 20 * 300, -9, 9),
              Trade(2, "BUY", 21 * 300, 22 * 300, -9, 9)]
    rows, excluded = examples(bars, trades, outcomes)
    assert [row["number"] for row in rows] == [1]
    assert excluded == {"history_missing": 1}
    assert rows[0]["features"]["spread_atr"] > 0
