import pytest
import hashlib
import sqlite3

from ramon.historical_benchmark import (
    ContrarianBaseline,
    PersistentForecastCache,
    PreviousBarBaseline,
    benchmark_database,
)
from ramon.history import ensure_history_db


def seed(db, rows=2400):
    ensure_history_db(db)
    data = []
    base = 1_800_000_000
    for i in range(rows):
        # Alternating drift plus a slow trend gives both directions without future data.
        drift = 0.18 if (i // 24) % 2 == 0 else -0.14
        price = 100.0 + i * 0.01 + drift * (i % 24)
        close = price + (0.08 if i % 3 else -0.05)
        data.append((
            "XAUUSD_KAGGLE", "M15", base + i * 900,
            price, max(price, close) + 0.35, min(price, close) - 0.35, close, 0,
        ))
    with sqlite3.connect(db) as conn:
        conn.executemany(
            """INSERT INTO history_bars
               (symbol,timeframe,time,open,high,low,close,spread_points)
               VALUES (?,?,?,?,?,?,?,?)""",
            data,
        )


def test_previous_bar_uses_only_last_completed_move():
    model = PreviousBarBaseline()
    closes = [100.0 + i * 0.1 for i in range(16)]
    forecast = model.forecast(closes, 4)
    assert forecast.median > closes[-1]
    assert len(forecast.median_path) == 4
    changed = closes[:-2] + [closes[-2], closes[-1] - 1.0]
    assert model.forecast(changed, 4).median != forecast.median


def test_benchmark_reports_chronological_folds_years_and_directions(tmp_path):
    db = tmp_path / "external.sqlite3"
    seed(db)
    report = benchmark_database(db, folds=5, stride=4, fallback_spread_points=42)

    assert report["dataset"]["bars"] == 2400
    assert set(report["models"]) == {
        "previous_bar", "momentum_4bar",
        "contrarian_previous_bar", "contrarian_momentum_4bar",
    }
    assert report["execution_assumptions"]["fallback_spread_points"] == 42

    for model in report["models"].values():
        assert len(model["folds"]) == 5
        starts = [fold["window"]["start_index"] for fold in model["folds"]]
        ends = [fold["window"]["end_index_exclusive"] for fold in model["folds"]]
        assert starts == sorted(starts)
        assert all(a <= b for a, b in zip(ends[:-1], starts[1:]))
        assert set(model["by_direction"]) == {"BUY", "SELL"}
        assert "profit_factor" in model["metrics"]
        assert "mean_r" in model["metrics"]
        assert "resolved_win_rate" in model["metrics"]
        assert "timeout_rate" in model["metrics"]
        assert set(model["directional_accuracy"]) == {"1", "4", "8", "16"}
        assert set(model["horizon_matrix"]) == {"1", "4", "8", "16"}
        assert set(model["regime_directional_accuracy"]) == {"1", "4", "8", "16"}
        assert set(model["regime_trade_matrix"]) == {"1", "4", "8", "16"}
        for horizon in ("1", "4", "8", "16"):
            directional = model["directional_accuracy"][horizon]
            assert directional["samples"] > 0
            assert 0 <= directional["accuracy"] <= 1
            assert "mean_signed_move" in directional
            assert "timeout_rate" in model["horizon_matrix"][horizon]["metrics"]
            assert model["regime_directional_accuracy"][horizon]
            regime_matrix = model["regime_trade_matrix"][horizon]
            horizon_trades = model["horizon_matrix"][horizon]["metrics"]["trades"]
            assert sum(m["trades"] for m in regime_matrix["by_regime"].values()) == horizon_trades
            if horizon_trades:
                assert regime_matrix["by_regime"]
                assert regime_matrix["stability"]
            for state, stability in regime_matrix["stability"].items():
                assert "overall" in stability
                assert "profitable_folds" in stability
                assert "positive_mean_r_folds" in stability
                assert len(stability["folds"]) == 5
                assert 0 <= stability["profitable_folds"] <= 5
                assert 0 <= stability["positive_mean_r_folds"] <= 5
            for state_metrics in model["regime_directional_accuracy"][horizon].values():
                if state_metrics["samples"]:
                    assert 0 <= state_metrics["accuracy"] <= 1


def test_benchmark_is_read_only(tmp_path):
    db = tmp_path / "external.sqlite3"
    seed(db)
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    benchmark_database(db, folds=3, stride=8)
    after = hashlib.sha256(db.read_bytes()).hexdigest()
    assert after == before


def test_custom_analysis_horizons_are_respected(tmp_path):
    db = tmp_path / "external.sqlite3"
    seed(db)
    report = benchmark_database(db, folds=3, stride=8, analysis_horizons=(1, 8))
    for model in report["models"].values():
        assert set(model["directional_accuracy"]) == {"1", "8"}
        assert set(model["horizon_matrix"]) == {"1", "8"}
        assert set(model["regime_trade_matrix"]) == {"1", "8"}


def test_contrarian_mirrors_direction():
    closes = [100.0 + i * 0.1 for i in range(16)]
    base = PreviousBarBaseline()
    contra = ContrarianBaseline(base)
    normal = base.forecast(closes, 4)
    flipped = contra.forecast(closes, 4)
    assert normal.median > closes[-1]
    assert flipped.median < closes[-1]
    assert len(flipped.median_path) == 4


def test_regime_accuracy_has_no_future_dependency(tmp_path):
    db = tmp_path / "external.sqlite3"
    seed(db, rows=1600)
    report = benchmark_database(db, folds=3, stride=16, analysis_horizons=(1,))
    for model in report["models"].values():
        regimes = model["regime_directional_accuracy"]["1"]
        assert regimes
        assert sum(v["samples"] for v in regimes.values()) > 0


def test_contrarian_never_emits_nonpositive_path():
    class ExtremeUp:
        def forecast(self, closes, horizon):
            anchor = closes[-1]
            path = tuple(anchor + 1000.0 * (i + 1) for i in range(horizon))
            return __import__("ramon.core", fromlist=["Forecast"]).Forecast(
                anchor + 900.0,
                anchor + 1000.0,
                anchor + 1100.0,
                path,
            )

    closes = [100.0 + i for i in range(16)]
    forecast = ContrarianBaseline(ExtremeUp()).forecast(closes, 4)
    assert forecast.low > 0
    assert forecast.median > 0
    assert forecast.high > 0
    assert all(value > 0 for value in forecast.median_path)
    assert forecast.low <= forecast.median <= forecast.high


def test_regime_trade_stability_sums_trade_metrics(tmp_path):
    db = tmp_path / "external.sqlite3"
    seed(db, rows=1800)
    report = benchmark_database(db, folds=3, stride=12, analysis_horizons=(4,))
    model = report["models"]["contrarian_previous_bar"]
    matrix = model["regime_trade_matrix"]["4"]
    total = sum(metrics["trades"] for metrics in matrix["by_regime"].values())
    horizon_total = model["horizon_matrix"]["4"]["metrics"]["trades"]
    assert total == horizon_total
    for state, row in matrix["stability"].items():
        assert len(row["folds"]) == 3
        assert row["folds_with_trades"] <= 3
        assert row["overall"]["trades"] == matrix["by_regime"][state]["trades"]


def test_persistent_forecast_cache_reuses_identical_context(tmp_path):
    from ramon.core import Forecast

    class CountingModel:
        def __init__(self):
            self.calls = 0

        def forecast(self, closes, horizon):
            self.calls += 1
            anchor = closes[-1]
            path = tuple(anchor + 0.1 * (i + 1) for i in range(horizon))
            return Forecast(path[-1] - 0.2, path[-1], path[-1] + 0.2, path)

    base = CountingModel()
    cache = PersistentForecastCache(base, tmp_path / "forecast-cache.sqlite3", "fake@1")
    closes = [100.0 + i * 0.01 for i in range(256)]
    first = cache.forecast(closes, 4)
    second = cache.forecast(closes, 4)
    assert first == second
    assert base.calls == 1
    assert cache.stats()["hits"] == 1
    assert cache.stats()["misses"] == 1
    cache.close()

    # Cache survives a new wrapper/process-like lifecycle.
    again = CountingModel()
    cache2 = PersistentForecastCache(again, tmp_path / "forecast-cache.sqlite3", "fake@1")
    third = cache2.forecast(closes, 4)
    assert third == first
    assert again.calls == 0
    assert cache2.stats()["hits"] == 1
    cache2.close()


def test_additional_model_can_use_independent_stride(tmp_path):
    from ramon.core import Forecast

    class FlatUp:
        def forecast(self, closes, horizon):
            anchor = closes[-1]
            path = tuple(anchor + 0.05 * (i + 1) for i in range(horizon))
            return Forecast(path[-1] - 0.2, path[-1], path[-1] + 0.2, path)

    db = tmp_path / "external.sqlite3"
    seed(db, rows=1600)
    report = benchmark_database(
        db,
        folds=3,
        stride=4,
        analysis_horizons=(1, 4),
        additional_models=(("fake_model", FlatUp(), 32),),
    )
    assert report["models"]["previous_bar"]["stride"] == 4
    assert report["models"]["fake_model"]["stride"] == 32
    assert set(report["models"]["fake_model"]["directional_accuracy"]) == {"1", "4"}


def test_chronos_cpu_workers_cli_flag_is_validated(monkeypatch, tmp_path):
    # Lightweight parser-level validation via direct argv; Chronos is not loaded.
    import sys
    from ramon import historical_benchmark as hb

    db = tmp_path / "external.sqlite3"
    seed(db, rows=1200)
    monkeypatch.setattr(sys, "argv", [
        "historical_benchmark",
        "--db", str(db),
        "--cpu-workers", "0",
    ])
    with pytest.raises(SystemExit):
        hb.main()


def test_gap_guard_excludes_entry_and_outcome_intervals(monkeypatch):
    from dataclasses import replace
    import ramon.historical_benchmark as lab
    from ramon.core import Bar, Settings

    contiguous = [Bar(i * 900, 100, 101, 99, 100) for i in range(270)]
    # The first sample is 256. A break at 258 invalidates H=4 samples
    # 256 and 257, while 258 onward is eligible again.
    bars = [replace(b, time=b.time + (900 if i >= 258 else 0))
            for i, b in enumerate(contiguous)]
    prefix = lab.m15_gap_prefix(bars)
    assert not lab.horizon_is_contiguous(prefix, 256, 4)
    assert not lab.horizon_is_contiguous(prefix, 257, 4)
    assert lab.horizon_is_contiguous(prefix, 258, 4)
    calls = []

    class NoForecast:
        def forecast(self, closes, horizon):
            calls.append(len(closes))
            return lab.Forecast(99, 101, 102)

    accuracy = lab.directional_accuracy(bars, NoForecast(), start=256,
                                       end=270, horizons=[4], stride=1)['4']
    assert accuracy['gap_skipped'] == 2
    assert len(calls) == 8
    calls.clear()
    lab.regime_directional_accuracy(bars, [42]*270, NoForecast(),
        symbol='X', point=.01, start=256, end=270, horizons=[4], stride=1,
        fallback_spread_points=42)
    assert len(calls) == 8
    decisions = []
    from types import SimpleNamespace
    monkeypatch.setattr(lab, 'evaluate', lambda market, model, settings:
        decisions.append(market.bars[-1].time) or SimpleNamespace(decision='WAIT'))
    _, counts = lab.simulate(bars, [42]*270, NoForecast(), symbol='X', point=.01,
        settings=replace(Settings(), horizon=4), start=256, end=270,
        stride=1, fallback_spread_points=42, roundtrip_cost_r=0)
    assert counts == {'decisions': 8, 'waits': 8, 'gap_skipped': 2}
    assert decisions[0] == bars[258].time
    # Non-increasing timestamps and weekends are discontinuities too.
    assert lab.m15_gap_prefix([contiguous[0], contiguous[0]]) == [0, 1]
    assert lab.m15_gap_prefix([contiguous[0], replace(contiguous[1], time=172800)]) == [0, 1]
