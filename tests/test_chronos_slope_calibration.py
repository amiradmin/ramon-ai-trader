from pathlib import Path
import sqlite3

from ramon.chronos_slope_calibration import (
    apply_calibration,
    load_horizon_calibrations,
    record_forecast_steps,
    resolve_due_forecasts,
)


def test_shadow_calibration_learns_bias_then_corrects(tmp_path: Path) -> None:
    db = tmp_path / "ramon.sqlite3"
    symbol = "XAUUSD_l"
    model = "autogluon/chronos-2-small"
    base = 2_000_000_000

    # Create 12 independent +15m samples with a stable +2.0 price over-prediction.
    for idx in range(12):
        captured = base + idx * 60
        record_forecast_steps(
            db,
            symbol=symbol,
            model=model,
            captured_utc=captured,
            source_mid=4000.0,
            steps=[4012.0],
        )
        resolved = resolve_due_forecasts(
            db,
            symbol=symbol,
            model=model,
            observed_utc=captured + 900,
            actual_mid=4010.0,
        )
        assert resolved == 1

    calibration = load_horizon_calibrations(
        db,
        symbol=symbol,
        model=model,
        horizons=1,
        minimum_samples=12,
    )[0]
    assert calibration.active is True
    assert calibration.samples == 12
    assert calibration.bias == 2.0
    assert calibration.mae == 2.0
    assert apply_calibration([4020.0], [calibration]) == [4018.0]


def test_shadow_calibration_stays_inactive_before_minimum_samples(tmp_path: Path) -> None:
    db = tmp_path / "ramon.sqlite3"
    symbol = "XAUUSD_l"
    model = "autogluon/chronos-2-small"
    base = 2_100_000_000

    for idx in range(5):
        captured = base + idx * 60
        record_forecast_steps(
            db,
            symbol=symbol,
            model=model,
            captured_utc=captured,
            source_mid=4000.0,
            steps=[4005.0],
        )
        resolve_due_forecasts(
            db,
            symbol=symbol,
            model=model,
            observed_utc=captured + 900,
            actual_mid=4004.0,
        )

    calibration = load_horizon_calibrations(
        db,
        symbol=symbol,
        model=model,
        horizons=1,
        minimum_samples=12,
    )[0]
    assert calibration.active is False
    assert calibration.samples == 5
    assert apply_calibration([4010.0], [calibration]) == [4010.0]


def test_stale_pending_forecast_is_not_resolved_with_current_price(tmp_path: Path) -> None:
    db = tmp_path / "ramon.sqlite3"
    symbol = "XAUUSD_l"
    model = "autogluon/chronos-2-small"
    captured = 2_200_000_000
    record_forecast_steps(
        db,
        symbol=symbol,
        model=model,
        captured_utc=captured,
        source_mid=4000.0,
        steps=[4005.0],
    )

    # Much later: do not poison the historical target with today's live midpoint.
    resolved = resolve_due_forecasts(
        db,
        symbol=symbol,
        model=model,
        observed_utc=captured + 5000,
        actual_mid=4100.0,
    )
    assert resolved == 0

    with sqlite3.connect(db) as con:
        row = con.execute(
            "SELECT actual_price,error FROM chronos_slope_forecasts"
        ).fetchone()
    assert row == (None, None)
