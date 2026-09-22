from __future__ import annotations

from ramon.daily_train import _promotion_gate
from ramon.replay import ReplayResult


def result(*, net_r: float, dd: float, buys: int = 10, sells: int = 10) -> ReplayResult:
    return ReplayResult(
        bars=1000,
        decisions=100,
        buys=buys,
        sells=sells,
        wins=12,
        losses=6,
        timed_out=2,
        net_r=net_r,
        max_drawdown_r=dd,
    )


def test_daily_promotion_gate_requires_real_improvement() -> None:
    incumbent = result(net_r=2.0, dd=3.0)
    challenger = result(net_r=3.0, dd=3.5)
    ok, reasons = _promotion_gate(
        incumbent,
        challenger,
        minimum_trades=20,
        minimum_improvement_r=0.5,
        maximum_drawdown_r=8.0,
    )
    assert ok
    assert reasons == []

    weak = result(net_r=2.2, dd=3.0)
    ok, reasons = _promotion_gate(
        incumbent,
        weak,
        minimum_trades=20,
        minimum_improvement_r=0.5,
        maximum_drawdown_r=8.0,
    )
    assert not ok
    assert reasons
