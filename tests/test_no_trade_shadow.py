import pytest

from ramon.no_trade_shadow import evaluate_shadow


def sample(key, net_r, veto):
    return {"opportunity_id": key, "net_r": str(net_r), "candidate_veto": str(veto)}


def test_vetoed_winners_and_losers_both_reported():
    rows = [sample("a", 2, 1), sample("b", -3, 1),
            sample("c", 1, 0), sample("d", -0.5, 0)]
    result = evaluate_shadow(rows)
    assert result["baseline"]["net"] == -0.5
    assert result["candidate_accepted"]["net"] == 0.5
    assert result["candidate_vetoed"]["net"] == -1
    assert result["realized_net_r_difference_if_vetoed"] == 1
    assert result["vetoed_winners"] == 1
    assert result["vetoed_losers"] == 1
    assert result["skipped_opportunity_rate"] == 0.5


def test_no_veto_and_all_veto_are_defined():
    a = evaluate_shadow([sample("a", 1, 0)])
    assert a["candidate_vetoed"]["trades"] == 0
    b = evaluate_shadow([sample("a", -1, 1)])
    assert b["candidate_accepted"]["trades"] == 0


@pytest.mark.parametrize("rows", [
    [],
    [sample("a", 1, 0), sample("a", -1, 1)],
    [sample("", 1, 0)],
    [sample("a", "nan", 1)],
    [sample("a", 1, "maybe")],
    [{"opportunity_id": "a", "candidate_veto": "1"}],
])
def test_rejects_unsafe_inputs(rows):
    with pytest.raises((ValueError, KeyError)):
        evaluate_shadow(rows)
