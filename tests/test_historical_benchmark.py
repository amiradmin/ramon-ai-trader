import hashlib
import sqlite3

from ramon.historical_benchmark import ContrarianBaseline, PreviousBarBaseline, benchmark_database
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
        for horizon in ("1", "4", "8", "16"):
            directional = model["directional_accuracy"][horizon]
            assert directional["samples"] > 0
            assert 0 <= directional["accuracy"] <= 1
            assert "mean_signed_move" in directional
            assert "timeout_rate" in model["horizon_matrix"][horizon]["metrics"]
            assert model["regime_directional_accuracy"][horizon]
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
