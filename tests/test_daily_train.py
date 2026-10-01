from __future__ import annotations

from ramon.core import Bar
from ramon.daily_train import _evaluate, _promotion_gate
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


def test_evaluate_uses_the_supplied_immutable_market_snapshot(monkeypatch) -> None:
    bars = tuple(Bar(i * 900, 100.0, 101.0, 99.0, 100.0) for i in range(300))
    spreads = tuple(42 for _ in bars)
    expected = result(net_r=1.0, dd=2.0)
    seen: list[tuple[object, object, object]] = []

    class FakeModel:
        pass

    monkeypatch.setattr("ramon.daily_train.model_name", lambda value: value)
    monkeypatch.setattr("ramon.daily_train.ChronosForecaster", lambda value, device: FakeModel())

    def fake_replay(got_bars, got_spreads, model, *, stride):
        seen.append((got_bars, got_spreads, model))
        assert stride == 4
        return expected

    monkeypatch.setattr("ramon.daily_train.replay", fake_replay)

    assert _evaluate(bars, spreads, "adapter", "cpu") is expected
    assert seen[0][0] is bars
    assert seen[0][1] is spreads
