import pytest

from ramon.loss_audit import audit, parse_report


def test_risk_shadow_excludes_legacy_and_keeps_later_period_separate():
    lines = []
    for number, version, risk, net, reason in [
        (1, "0.28", 5, 5, "DEAL_REASON_EXPERT / maximum_hold_bars"),
        (2, "0.29", 9, 2, "DEAL_REASON_EXPERT / maximum_hold_bars"),
        (3, "0.31", 15, -15, "DEAL_REASON_SL / stop_loss"),
        (4, "0.41", 8, 3, "DEAL_REASON_EXPERT / tp1_stall_exit"),
    ]:
        lines.append(
            f"{number:03} | {'WIN ' if net > 0 else 'LOSS'} | BUY  | "
            f"2026-09-29 01:00 UTC -> 2026-09-29 01:30 UTC | "
            f"net={net:+.2f} | risk={risk:.2f} | R={net/risk:+.3f} | "
            f"{reason} | model=x bundle=NONE EA={version} label=LEARNABLE"
        )
    rows = parse_report("\n".join(lines))
    result = audit(rows, cap_usd=0.10, holdout_count=1)
    assert result["report_trades"]["count"] == 4
    assert result["exit_decomposition"]["stop_loss_median_minutes"] == 30
    shadow = result["fixed_cap_shadow"]
    assert shadow["exact_sizing_proxy_trades"] == 3
    assert shadow["earlier"]["within_cap"]["net_units"] == 2
    assert shadow["earlier"]["above_cap"]["net_units"] == -15
    assert shadow["later_untouched"]["within_cap"]["net_units"] == 3


def test_missing_or_repeated_trade_numbers_rejected():
    with pytest.raises(ValueError):
        parse_report("no trades")
    row = ("001 | LOSS | BUY | 2026-09-29 01:00 UTC -> 2026-09-29 01:30 UTC "
           "| net=-1.00 | risk=2.00 | R=-0.500 | DEAL_REASON_SL / stop_loss "
           "| model=x EA=0.41")
    with pytest.raises(ValueError):
        parse_report(row + "\n" + row)
