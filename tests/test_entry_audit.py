from ramon.entry_audit import print_entry_audit, reentry_candidates, trade_role


def trade(index, opened, closed, *, role="MAIN", direction="BUY", net=-6, reason="DEAL_REASON_SL", **extra):
    return dict(trade_key=f"broker:123:{index}", opened=opened, closed=closed,
                opened_utc_offset_seconds=0, closed_utc_offset_seconds=0,
                trade_role=role, direction=direction, net_units=net,
                exit_reason=reason, **extra)


def test_role_never_guessed_from_size_and_conflicts_remain_unknown():
    assert trade_role({"net_units": 2, "entry_ea_version": "0.53.5"}) == "UNKNOWN"
    assert trade_role({"entry_magic": 26092213}) == "SMALL"
    assert trade_role({"trade_role": "MAIN", "entry_magic": 26092213}) == "UNKNOWN"


def test_reentry_after_first_sl_is_distinct_from_two_sl_review():
    rows = [trade(1, 0, 100), trade(2, 120, 200), trade(3, 220, 300)]
    result = reentry_candidates(rows[::-1])
    assert [(r["trade"]["trade_key"], r["observed_sl_streak"], r["gap_seconds"]) for r in result] == [
        ("broker:123:2", 1, 20), ("broker:123:3", 2, 20)]


def test_roles_accounts_directions_wins_and_manual_closes_break_comparisons():
    first = trade(1, 0, 100)
    for changed in (dict(role="SMALL"), dict(direction="SELL")):
        assert reentry_candidates([first, trade(2, 120, 200, **changed)]) == []
    other = trade(2, 120, 200)
    other["trade_key"] = "broker:456:2"
    assert reentry_candidates([first, other]) == []
    for changed in (dict(net=1), dict(reason="DEAL_REASON_CLIENT")):
        rows = [first, trade(2, 110, 200, **changed), trade(3, 220, 300)]
        assert not any(r["trade"]["trade_key"] == "broker:123:3" for r in reentry_candidates(rows))


def test_utc_conversion_boundary_and_missing_offsets():
    first = trade(1, 0, 10900)
    first["closed_utc_offset_seconds"] = 10800  # close=100 UTC
    assert reentry_candidates([first, trade(2, 1899, 2000)])[0]["gap_seconds"] == 1799
    assert reentry_candidates([first, trade(2, 1900, 2000)]) == []
    first["closed_utc_offset_seconds"] = None
    assert reentry_candidates([first, trade(2, 120, 200)]) == []


def test_report_uses_entry_time_cap_and_exposes_unknowns(capsys):
    main = trade(1, 0, 100, risk_budget_units=6, initial_risk_units=14.38,
                 max_executable_risk_usd=.20, money_units_per_usd=100, min_lot_override_used=1)
    small = trade(2, 110, 200, role="SMALL", risk_budget_units=2, initial_risk_units=2.1,
                  max_executable_risk_usd=.02, money_units_per_usd=100)
    unknown = trade(3, 220, 300, role=None)
    print_entry_audit([main, small, unknown])
    report = capsys.readouterr().out
    assert "MAIN | trades=1" in report and "SMALL | trades=1" in report
    assert "UNKNOWN | trades=1" in report
    assert "above preferred budget=1 | above entry-time cap=0" in report
    assert "above preferred budget=1 | above entry-time cap=1" in report
    assert "does not prove a sizing bug" in report
