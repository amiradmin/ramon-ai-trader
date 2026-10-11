from ramon.executed_trade_dataset_builder import build_executed_trade_dataset


def _candles():
    def series(step):
        return {
            ts: (ts, 10, 11, 9, 10.5)
            for ts in range(0, 21600, step)
        }
    return {"M5": series(300), "M15": series(900),
            "H1": series(3600)}


def _decision(key, captured=10800, signal=9900, direction="BUY"):
    return {"sample_key": key, "symbol": "XAUUSD_l",
            "signal_bar_time": signal + 10800, "captured": captured,
            "direction": direction}


def _trade(key, trade_key, opened=10820, closed=11100,
           direction="BUY"):
    return {"trade_key": trade_key, "sample_key": key,
            "symbol": "XAUUSD_l", "opened": opened, "closed": closed,
            "direction": direction, "net_r": -0.25, "net_units": -3.0,
            "exit_reason": "DEAL_REASON_SL", "training_status": "LEARNABLE"}


def test_trade_first_retains_two_trades_from_same_signal_bar():
    decisions = [_decision("first"), _decision("second", captured=10810)]
    trades = [_trade("first", "T1"), _trade("second", "T2", opened=10830)]
    rows, report = build_executed_trade_dataset(
        decisions, trades, _candles(), broker_utc_offset_seconds=10800)
    assert len(rows) == 2
    assert {row["trade_key"] for row in rows} == {"T1", "T2"}
    assert report["counts"]["complete_context_trades"] == 2
    assert rows[0]["signal_bar_utc"] == 9900
    assert rows[0]["m5_last_close"] == 10.5


def test_unmatched_and_ambiguous_trades_fail_closed():
    decisions = [_decision("duplicate"), _decision("duplicate")]
    trades = [_trade("unknown", "T1"), _trade("duplicate", "T2")]
    rows, report = build_executed_trade_dataset(
        decisions, trades, _candles(), broker_utc_offset_seconds=10800)
    assert not rows
    assert report["counts"]["unmatched_trade"] == 1
    assert report["counts"]["ambiguous_decision"] == 1


def test_noncausal_and_future_trade_excluded():
    decisions = [_decision("later", captured=10800),
                 _decision("future", captured=10800, signal=11700)]
    trades = [_trade("later", "T1", opened=10799),
              _trade("future", "T2")]
    rows, report = build_executed_trade_dataset(
        decisions, trades, _candles(), broker_utc_offset_seconds=10800)
    assert not rows
    assert report["counts"]["invalid_or_noncausal_match"] == 2


def test_missing_m5_is_not_filled_using_future_candle():
    candles = _candles()
    del candles["M5"][10500]
    rows, report = build_executed_trade_dataset(
        [_decision("one")], [_trade("one", "T1")], candles,
        broker_utc_offset_seconds=10800)
    assert rows == []
    assert report["counts"]["M5_missing_context"] == 1


def test_outcome_return_not_used_in_features():
    decisions = [_decision("one")]
    t1 = _trade("one", "T1")
    t2 = dict(t1, net_r=5.0, net_units=1000, closed=15000)
    a, _ = build_executed_trade_dataset(
        decisions, [t1], _candles(), broker_utc_offset_seconds=10800)
    b, _ = build_executed_trade_dataset(
        decisions, [t2], _candles(), broker_utc_offset_seconds=10800)
    assert a[0]["m15_return_2bars"] == b[0]["m15_return_2bars"]
    assert a[0]["h1_last_close"] == b[0]["h1_last_close"]
    assert a[0]["net_r"] != b[0]["net_r"]
