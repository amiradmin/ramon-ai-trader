import pytest

from ramon.tick_path_dataset import label_paths


def opp(key="a", **changes):
    row = {"opportunity_id": key, "direction": "BUY", "entry_ms": "1000",
           "entry_price": "100.2", "stop": "99", "target": "102",
           "horizon_ms": "3000"}
    row.update(changes)
    return row


def quote(t, bid=100, ask=100.2):
    return {"time_ms": str(t), "bid": str(bid), "ask": str(ask)}


def test_uses_only_requested_interval_and_first_touch():
    rows = [quote(900, bid=102.5, ask=102.7), quote(1000), quote(2000, 102, 102.2),
            quote(3000, 98, 98.2), quote(5000)]
    labeled = label_paths([opp()], rows, max_gap_ms=1000)
    assert labeled[0]["label"] == "TP"
    assert labeled[0]["touch_ms"] == 2000
    assert labeled[0]["quote_count_in_window"] == 3


def test_uncovered_paths_are_not_wins_or_losses():
    labeled = label_paths([opp()], [quote(900), quote(3500, 98, 98.2)],
                          max_gap_ms=1000)
    assert labeled[0]["label"] == "UNKNOWN"


def test_empty_quote_archive_yields_unknown():
    assert label_paths([opp()], [])[0]["label"] == "UNKNOWN"


@pytest.mark.parametrize("opportunities,quotes", [
    ([opp(), opp()], [quote(1000)]),
    ([opp("")], [quote(1000)]),
    ([opp(direction="WAIT")], [quote(1000)]),
    ([opp()], [quote(2000), quote(1000)]),
    ([opp()], [quote(1000), quote(1000)]),
])
def test_rejects_ambiguous_inputs(opportunities, quotes):
    with pytest.raises(ValueError):
        label_paths(opportunities, quotes)
