"""Read-only opportunity report smoke tests."""
from ramon.opportunity_review import review, _group_trades

def test_invalid_time_window_never_queries_database():
    assert review(since=100, until=100)["reason"] == "INVALID_TIME_WINDOW"

def test_loss_grouping_is_descriptive():
    rows=[{"exit_reason":"DEAL_REASON_SL","net_units":-2.0},
          {"exit_reason":"DEAL_REASON_SL","net_units":-3.0},
          {"exit_reason":"DEAL_REASON_EXPERT","net_units":-1.0}]
    r=_group_trades(rows)
    assert r[0]=={"reason":"DEAL_REASON_SL","count":2,"net_units":-5.0}

def test_requires_postgres(monkeypatch):
    monkeypatch.setenv("RAMON_STORAGE_BACKEND","sqlite")
    assert review(since=100,until=101)["reason"]=="POSTGRES_REQUIRED"
