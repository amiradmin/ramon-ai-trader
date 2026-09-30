from types import SimpleNamespace

import pytest

from ramon.core import Bar, Market
from ramon.covariates import CovariateForecaster, past_covariates
from ramon.research_compare import data_coverage, research_compare
from test_compare import RisingForecast, seed


class Array:
    def __init__(self, rows):
        self.rows = rows

    def tolist(self):
        return self.rows


class Pipeline:
    def __init__(self):
        self.tasks = []

    def predict_quantiles(self, tasks, *, prediction_length, quantile_levels):
        self.tasks.extend(tasks)
        task = tasks[0]
        assert "future_covariates" not in task
        assert all(len(v) == len(task["target"]) for v in task["past_covariates"].values())
        end = task["target"][-1]
        return [[Array([[end-2, end+.5*(i+1), end+3] for i in range(prediction_length)])]], None


def model():
    base = RisingForecast()
    base.model_id, base.revision = "test/fake", "fixture"
    base._np = SimpleNamespace(asarray=lambda x, dtype: list(x), float32=float)
    base.pipeline = Pipeline()
    return base


def test_covariates_are_causal_prefixes_not_future_values():
    bars = [Bar(i+1, 100+i, 102+i, 99+i, 101+i) for i in range(30)]
    for stop in range(1, 30):
        prefix = past_covariates(bars[:stop])
        complete = past_covariates(bars)
        assert all(values == complete[name][:stop] for name, values in prefix.items())


def test_adapter_uses_aligned_ohlc_context_and_valid_quantile_path():
    bars = tuple(Bar(i+1, 100+i, 102+i, 99+i, 101+i) for i in range(30))
    base = model()
    bound = CovariateForecaster(base).for_market(Market("XAUUSD_l", "M15", 130, 130.1, .01, bars))
    result = bound.forecast([bar.close for bar in bars[-16:]], 4)
    assert result.median_path == (130.5, 131, 131.5, 132)
    assert len(base.pipeline.tasks[0]["target"]) == 16
    with pytest.raises(ValueError, match="alignment"):
        bound.forecast([42]*16, 4)


def test_offline_compare_records_all_three_models_without_touching_database(tmp_path):
    db = tmp_path / "data.db"
    seed(db)
    before = db.read_bytes()
    report = research_compare(db, model(), cost_r=0)
    assert set(report["results"]) == {"momentum_baseline", "chronos", "chronos_past_covariates"}
    assert report["live_execution_effect"] == "NONE"
    assert report["promotion_allowed"] is False
    assert db.read_bytes() == before


def test_missing_spread_never_falls_back_in_experiment(tmp_path):
    db = tmp_path / "data.db"
    seed(db, missing=True)
    with pytest.raises(ValueError, match="without recorded spread"):
        research_compare(db, model(), cost_r=0)


def test_nonfinite_cost_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="finite"):
        research_compare(tmp_path / "does-not-exist.db", model(), cost_r=float("nan"))


def test_coverage_separates_bar_count_from_missing_holdout_spreads():
    bars = [Bar(i+1, 100, 101, 99, 100) for i in range(1000)]
    spreads = [42]*1000
    spreads[850] = 0
    report = data_coverage(bars, spreads)
    assert report["total_m15_bars"] == 1000
    assert report["holdout_bars"] == 200
    assert report["missing_holdout_spreads"] == 1
    assert report["missing_spread_examples"] == [{"mt5_time": 851, "spread_points": 0}]
    assert not report["ready"]
    assert not data_coverage([], [])["ready"]
