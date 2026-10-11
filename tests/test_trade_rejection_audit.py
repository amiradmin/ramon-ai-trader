from ramon.trade_rejection_audit import inspect_trades


def candles():
    return {
        tf: {stamp: (stamp, 10.0, 11.0, 9.0, 10.5)
             for stamp in range(0, 21600, step)}
        for tf, step in (("M5", 300), ("M15", 900), ("H1", 3600))
    }


def decision(key, captured=10800, signal=9900, direction="BUY"):
    return {"symbol": "XAUUSD_l", "sample_key": key,
            "captured": captured, "signal_bar_time": signal + 10800,
            "direction": direction}


def trade(key, trade_key, opened=10820, closed=11100, direction="BUY"):
    return {"symbol": "XAUUSD_l", "sample_key": key,
            "trade_key": trade_key, "opened": opened, "closed": closed,
            "direction": direction, "net_r": -0.2, "net_units": -2}


def test_reports_accepted_and_independent_causal_rejection_codes():
    decisions = [decision("ok"), decision("old"),
                 decision("wrong", direction="SELL")]
    trades = [trade("ok", "T1"),
              trade("old", "T2", opened=10700),
              trade("wrong", "T3"),
              trade("missing", "T4")]
    details, report = inspect_trades(
        decisions, trades, candles(), broker_utc_offset_seconds=10800)
    assert [d["reason"] for d in details] == [
        "COMPLETE_CONTEXT", "TRADE_OPEN_BEFORE_DECISION",
        "DIRECTION_MISMATCH", "UNMATCHED_DECISION"]
    assert report["trades_scanned"] == 4


def test_missing_m5_stamps_are_exported_for_mt5_backfill():
    bars = candles()
    del bars["M5"][10500]
    details, report = inspect_trades(
        [decision("a")], [trade("a", "T1")], bars,
        broker_utc_offset_seconds=10800)
    assert details[0]["reason"] == "M5_MISSING_OR_INVALID_CONTEXT"
    assert details[0]["missing_candle_open_utc"] == 10500
    assert report["missing_candle_open_timestamps"]["M5"][0]["open_utc"] == 10500


def test_multiple_snapshot_same_signal_bar_not_automatically_rejected():
    decisions = [decision("a"), decision("b")]
    trades = [trade("a", "T1"), trade("b", "T2", opened=10840)]
    details, report = inspect_trades(
        decisions, trades, candles(), broker_utc_offset_seconds=10800)
    assert report["first_rejection_counts"]["COMPLETE_CONTEXT"] == 2
    assert len(details) == 2


def test_invalid_signal_clock_does_not_leak_future_candles():
    d = decision("a", captured=10800, signal=11700)
    details, report = inspect_trades(
        [d], [trade("a", "T1")], candles(),
        broker_utc_offset_seconds=10800)
    assert details[0]["reason"] == "SIGNAL_BAR_AFTER_CAPTURE"
    assert "COMPLETE_CONTEXT" not in report["first_rejection_counts"]
