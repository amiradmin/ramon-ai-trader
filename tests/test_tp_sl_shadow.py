import pytest

from ramon.tp_sl_shadow import Quote, PathRequest, first_touch, calibration_report


def q(t, bid, ask):
    return Quote(t, bid, ask)


def req(direction="BUY", **kw):
    entry = 100.2 if direction == "BUY" else 100.0
    sl, tp = (99.0, 102.0) if direction == "BUY" else (101.0, 98.0)
    return PathRequest(direction, 1000, entry, sl, tp, 3000, max_gap_ms=1000, **kw)


def test_buy_exits_at_bid_and_first_observed_touch():
    assert first_touch(req(), [q(1000, 100, 100.2),
                               q(1800, 99, 99.2),
                               q(2500, 103, 103.2)])["label"] == "SL"


def test_sell_exits_at_ask():
    result = first_touch(req("SELL"), [q(1000, 99.8, 100),
                                         q(1700, 98.0, 98.2),
                                         q(2300, 97.7, 97.9)])
    assert result["label"] == "TP"
    assert result["touch_ms"] == 2300


def test_no_touch_and_coverage_gaps_are_censored():
    assert first_touch(req(), [q(1000, 100, 100.2),
                               q(2000, 100, 100.2),
                               q(3000, 100, 100.2),
                               q(4000, 100, 100.2)])["label"] == "TIMEOUT"
    assert first_touch(req(), [q(1000, 100, 100.2),
                               q(3500, 103, 103.2)])["label"] == "UNKNOWN"
    assert first_touch(req(), [q(3000, 100, 100.2)])["label"] == "UNKNOWN"


def test_invalid_quotes_and_out_of_order_data_fail():
    with pytest.raises(ValueError):
        Quote(1000, 101, 100)
    with pytest.raises(ValueError):
        first_touch(req(), [q(2000, 100, 100.2), q(1000, 100, 100.2)])
    with pytest.raises(ValueError):
        req("WAIT")


def test_calibration_score_does_not_turn_timeout_into_loss():
    result = calibration_report([
        {"opportunity_id": "a", "label": "TP", "p_tp_first": "0.9"},
        {"opportunity_id": "b", "label": "SL", "p_tp_first": "0.2"},
        {"opportunity_id": "c", "label": "TIMEOUT", "p_tp_first": ""},
        {"opportunity_id": "d", "label": "UNKNOWN", "p_tp_first": ""},
    ], min_samples=2)
    assert result["resolved"] == 2
    assert result["brier_score"] == 0.025
    assert result["minimum_samples_met"] is True
    assert result["labels"]["TIMEOUT"] == 1


@pytest.mark.parametrize("rows", [
    [{"opportunity_id": "a", "label": "TP"}],
    [{"opportunity_id": "a", "label": "TP", "p_tp_first": "1.2"}],
    [{"opportunity_id": "a", "label": "SL", "p_tp_first": "nan"}],
    [{"opportunity_id": "a", "label": "WAIT", "p_tp_first": "0.5"}],
    [{"opportunity_id": "a", "label": "TP", "p_tp_first": "0.3"},
     {"opportunity_id": "a", "label": "SL", "p_tp_first": "0.4"}],
])
def test_rejects_invalid_calibration(rows):
    with pytest.raises(ValueError):
        calibration_report(rows)
