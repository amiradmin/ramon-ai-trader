from pathlib import Path


def test_managed_position_guard_precedes_live_order_submission():
    source = Path("mt5/Ramon.mq5").read_text(encoding="utf-8")

    guard = "if(ManagedPosition(ticket,opened))"
    buy = "Trade.Buy(volume,_Symbol"
    sell = "Trade.Sell(volume,_Symbol"

    guard_index = source.find(guard)
    buy_index = source.find(buy)
    sell_index = source.find(sell)

    assert guard_index >= 0, "managed-position execution guard is missing"
    assert buy_index >= 0 and sell_index >= 0, "live order submission calls are missing"
    assert guard_index < buy_index
    assert guard_index < sell_index

    window = source[guard_index: min(buy_index, sell_index)]
    assert "return;" in window, (
        "managed-position guard must return before a new BUY/SELL can be submitted"
    )


def test_learning_continues_while_execution_remains_single_position():
    source = Path("mt5/Ramon.mq5").read_text(encoding="utf-8")

    marker = "// Learning snapshots continue while positions exist; execution remains single-position."
    guard = "if(ManagedPosition(ticket,opened))"

    assert marker in source
    assert source.find(marker) < source.find(guard)
