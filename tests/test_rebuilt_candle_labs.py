"""Safety/regression checks for reconstructed, observation-only research modules."""
import sqlite3

import pytest

from ramon.core import Bar
from ramon.moving_average_lab import features, dataset, evaluate
from ramon.candle_direction_lab import (
    build_samples, split, closed_context, temper, replay,
)
from ramon.candle_forward_recorder import Recorder, summary, read_candidate


def bars(count=110):
    return [
        Bar(time=900 * (i + 1), open=2000 + i * .1,
            high=2001 + i * .1, low=1999 + i * .1,
            close=2000 + i * .1)
        for i in range(count)
    ]


def test_ma_only_uses_completed_context():
    source = bars()
    before = features(source[:64])
    changed_future = list(source)
    changed_future[64] = Bar(changed_future[64].time, 9e3, 9e3, 9e3, 9e3)
    assert features(changed_future[:64]) == before
    assert set(before) == {
        "ret_1_atr", "ret_4_atr", "ema_fast_distance_atr",
        "ema_slow_distance_atr", "ema_gap_atr",
        "sma_distance_atr", "range_8_atr", "body_atr"
    }


def test_ma_rejects_missing_bars_and_purges_labels():
    source = bars()
    with pytest.raises(ValueError):
        features(source[:10])
    corrupted = list(source[:64])
    corrupted[-1] = Bar(time=corrupted[-2].time + 1800, open=1, high=2, low=1, close=2)
    with pytest.raises(ValueError):
        features(corrupted)
    samples = dataset(source, horizon=3)
    report = evaluate(samples, folds=2)
    assert len(report["folds"]) == 2
    assert all(f["test"] > 0 for f in report["folds"])


def test_candle_split_no_future_label_overlap():
    samples = build_samples(bars(150), horizon=3)
    train, valid, test = split(samples)
    assert train[-1].label_time < valid[0].time
    assert valid[-1].label_time < test[0].time
    assert all(x.label in ("UP", "FLAT", "DOWN") for x in samples)
    assert closed_context(bars(), 64 * 900, 64, 900)[-1].time == 63 * 900


def test_candle_probability_validation_and_no_pnl_claim():
    with pytest.raises(ValueError):
        temper([.5, .5, -.1])
    samples = build_samples(bars())
    result = replay([[.05, .05, .9]] * len(samples), samples)
    assert result["profit_claim"] is False
    assert len(result["decisions"]) == len(samples)


def test_recorder_rejects_future_context_and_deduplicates(tmp_path):
    candidate = {
        "symbol": "XAUUSD_l", "feature_cutoff_utc": 9000,
        "horizon_bars": 1, "probabilities": [.1, .2, .7],
        "source_recorded_utc": 9010, "provenance": {"model": "shadow"}
    }
    with pytest.raises(ValueError):
        read_candidate(candidate, {}, now=8999, source_recorded=8999)
    recorder = Recorder(None, tmp_path / "predictions.db")
    assert recorder.tick(candidate, now=9020)["inserted"]
    assert not recorder.tick(candidate, now=9020)["inserted"]
    recorder.close()
    assert summary(tmp_path / "predictions.db") == {
        "predictions": 1, "resolved": 0, "unresolved": 1, "live_trading": False}
