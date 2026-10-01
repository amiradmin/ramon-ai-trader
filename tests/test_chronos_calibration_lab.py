import math

from ramon.chronos_calibration_lab import (
    CalibrationRow,
    fit_ridge,
    metrics,
    walk_forward_folds,
)


def row(i: int, raw: float, target: float, regime=None):
    if regime is None:
        regime = (0.0, 0.0, 0.0, 1.0, 0.5)
    return CalibrationRow(
        signal_bar_time=i * 900,
        bar_index=i,
        target_index=i + 4,
        close=100.0,
        atr=2.0,
        raw_forecast=100.0 + raw * 2.0,
        raw_move_atr=raw,
        target_close=100.0 + target * 2.0,
        target_move_atr=target,
        regime=tuple(regime),
    )


def test_ridge_linear_calibration_learns_bias_and_scale():
    features = [(float(x),) for x in range(-5, 5)]
    targets = [1.5 * x - 0.25 for (x,) in features]
    model = fit_ridge(features, targets, l2=0.0)
    prediction = model.predict((4.0,))
    assert abs(prediction - (1.5 * 4.0 - 0.25)) < 1e-8


def test_metrics_reports_error_and_direction_accuracy():
    rows = [
        row(1, 1.0, 0.5),
        row(2, -1.0, -0.5),
        row(3, 0.2, -0.2),
    ]
    m = metrics(rows, [0.6, -0.4, 0.1])
    assert m["n"] == 3
    assert abs(m["mae_atr"] - (0.1 + 0.1 + 0.3) / 3) < 1e-12
    assert math.isclose(m["direction_acc"], 200.0 / 3.0)


def test_walk_forward_purges_rows_whose_target_overlaps_test_start():
    rows = [row(i, 0.1, 0.1) for i in range(1, 61)]
    folds = walk_forward_folds(rows, folds=3, horizon=4, initial_fraction=0.4)
    assert len(folds) == 3
    for fold in folds:
        assert fold.development
        assert fold.test
        test_start = fold.test[0].bar_index
        assert all(dev.target_index < test_start for dev in fold.development)
        assert fold.development[-1].bar_index < test_start


def test_regime_width_is_supported_by_ridge():
    rows = [
        (float(i), float(i % 3), float(i % 5))
        for i in range(1, 30)
    ]
    targets = [0.3 * x[0] - 0.1 * x[1] + 0.2 * x[2] for x in rows]
    model = fit_ridge(rows, targets, l2=1.0)
    value = model.predict((2.5, 1.0, 4.0))
    assert math.isfinite(value)
