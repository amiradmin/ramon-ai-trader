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

    def fake_replay(got_bars, got_spreads, model, *, stride, **kwargs):
        seen.append((got_bars, got_spreads, model))
        assert stride == 4
        assert kwargs["require_recorded_spreads"] is True
        assert kwargs["roundtrip_cost_r"] == .1
        return expected

    monkeypatch.setattr("ramon.daily_train.replay", fake_replay)

    assert _evaluate(bars, spreads, "adapter", "cpu") is expected
    assert seen[0][0] is bars
    assert seen[0][1] is spreads


def test_daily_waits_before_training_when_micro_context_missing(tmp_path, monkeypatch, capsys):
    import sys, json
    from ramon import daily_train
    monkeypatch.setattr(sys, "argv", ["daily_train", "--db", str(tmp_path/"x.db")])
    candles = tuple(Bar(1700000000+i*900,100,101,99,100) for i in range(4000))
    monkeypatch.setattr(daily_train, "history_count", lambda *a: 4000)
    monkeypatch.setattr(daily_train, "load_bars", lambda *a: (candles,(10,)*4000))
    monkeypatch.setattr(daily_train, "load_completed_micro_history", lambda *a: {})
    monkeypatch.setattr(daily_train, "train_checkpoint", lambda **kw: (_ for _ in ()).throw(AssertionError("must not train")))
    daily_train.main()
    assert json.loads(capsys.readouterr().out)["status"] == "waiting_for_evaluation_context"


def test_micro_loader_uses_only_completed_recorded_candles(tmp_path):
    import sqlite3
    from ramon.history import ensure_history_db, load_completed_micro_history
    db=tmp_path/"history.db"; ensure_history_db(db)
    bar=Bar(1700000100,100,101,99,100); quote=bar.time+900
    with sqlite3.connect(db) as conn:
        for t in range(quote-240,quote+61,60):
            conn.execute("INSERT INTO history_bars(symbol,timeframe,time,open,high,low,close,spread_points) VALUES(?,?,?,?,?,?,?,?)", ('XAUUSD_l','M1',t,100,101,99,100,10))
    result=load_completed_micro_history(db,'XAUUSD_l',(bar,))
    assert [b.time for b in result[bar.time]] == list(range(quote-240,quote,60))
    with sqlite3.connect(db) as conn:
        conn.execute("DELETE FROM history_bars WHERE time=?",(quote-120,))
    assert load_completed_micro_history(db,'XAUUSD_l',(bar,)) == {}
