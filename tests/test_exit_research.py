import sqlite3

import pytest

from ramon.exit_research import persist_observation, simulate_path


def path():
    return [dict(quote_time=100+i*30, profit_units=v, volume=.01)
            for i,v in enumerate((0., .8, .7, .3, -.6, -.7))]


def trade():
    return dict(opened=100, closed=280, initial_risk_units=1., net_units=-1.,
                planned_volume=.01, commission_units=-.02, fee_units=0.)


def test_early_policy_uses_first_observed_trigger_and_fee_buffer():
    result = simulate_path(trade(), path(), fee_buffer_r=.05)
    assert result["comparable"]
    assert result["outcomes_r"]["lock_half_r_giveback_quarter_r"] == pytest.approx(.23)
    assert result["outcomes_r"]["adverse_half_r_after_120s"] == pytest.approx(-.67)
    assert result["outcomes_r"]["actual"] == -1.


def test_post_close_marks_missing_boundaries_and_partials_rejected():
    rows = path()
    assert not simulate_path(trade(), rows[3:])["comparable"]
    assert not simulate_path(trade(), rows[:3])["comparable"]
    rows[2]["volume"] = .005
    assert not simulate_path(trade(), rows)["comparable"]
    assert not simulate_path(trade(), [dict(quote_time=300,profit_units=100,volume=.01)])["comparable"]


def test_observation_idempotent_and_invalid_price_rejected(tmp_path):
    db = str(tmp_path / "history.sqlite3")
    payload = dict(quote_time=100,bid=10.,ask=10.1,
                   position_observation=dict(trade_key="a",profit_units=.8,volume=.01))
    assert persist_observation(db,payload,110)
    assert persist_observation(db,payload,115)
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT COUNT(*) FROM position_observations").fetchone()[0] == 1
    payload["ask"] = 9
    with pytest.raises(ValueError):
        persist_observation(db,payload,120)


def test_unknown_commission_is_not_assumed_zero():
    row = trade()
    row["commission_units"] = None
    assert not simulate_path(row,path())["comparable"]
