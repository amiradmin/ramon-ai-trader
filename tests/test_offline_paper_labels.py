from ramon.offline_paper_labels import classify_path


def bars(start=120, n=30, high=100.1, low=99.9, close=100.0):
    return [(start+i*60, 100.0, high, low, close) for i in range(n)]


def check(direction, path, decision_time=61):
    return classify_path(decision_time=decision_time, direction=direction,
        bars=path, spread_price=0.02, target_price=0.2,
        stop_price=0.2, max_minutes=30)


def test_ambiguous_same_bar_cannot_be_labeled_win():
    assert check("BUY", bars(high=100.5, low=99.5))["status"] == "AMBIGUOUS_BOTH_BARRIERS"


def test_buy_and_sell_wins_charge_spread():
    assert check("BUY", bars(high=100.3, low=99.95))["status"] == "TP"
    assert check("SELL", bars(high=100.05, low=99.7))["status"] == "TP"


def test_wait_never_becomes_fake_trade():
    assert check("WAIT", bars())["status"] == "SKIP_NO_DIRECTION"


def test_gap_and_missing_horizon_are_rejected():
    path=bars()
    path[2]=(301,*path[2][1:])
    assert check("BUY",path)["status"]=="SKIP_GAPPED_BARS"
    assert check("BUY",bars(n=29))["status"]=="SKIP_INCOMPLETE_HORIZON"


def test_after_quote_not_partial_candle():
    path=bars(start=60, n=31)
    assert check("BUY",path,decision_time=61)["minute"]==30


def test_time_exit_net_of_spread():
    result=check("BUY", bars(high=100.1, low=99.9))
    assert result["status"]=="TIME_EXIT"
    assert abs(result["net_price"]+0.02)<1e-9
