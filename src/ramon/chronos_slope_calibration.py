from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sqlite3
import statistics


@dataclass(frozen=True, slots=True)
class HorizonCalibration:
    horizon_bars: int
    samples: int = 0
    bias: float = 0.0
    mae: float = 0.0
    active: bool = False


def _ensure_table(db: str | Path) -> Path:
    path = Path(db).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path, timeout=10) as con:
        con.execute(
            """CREATE TABLE IF NOT EXISTS chronos_slope_forecasts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                model TEXT NOT NULL,
                captured_utc INTEGER NOT NULL,
                captured_bucket INTEGER NOT NULL,
                source_mid REAL NOT NULL,
                horizon_bars INTEGER NOT NULL,
                target_utc INTEGER NOT NULL,
                raw_forecast REAL NOT NULL,
                actual_price REAL,
                resolved_utc INTEGER,
                error REAL,
                UNIQUE(symbol, model, captured_bucket, horizon_bars)
            )"""
        )
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_chronos_slope_pending "
            "ON chronos_slope_forecasts(symbol, model, target_utc, resolved_utc)"
        )
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_chronos_slope_resolved "
            "ON chronos_slope_forecasts(symbol, model, horizon_bars, resolved_utc DESC)"
        )
        con.commit()
    return path


def resolve_due_forecasts(
    db: str | Path,
    *,
    symbol: str,
    model: str,
    observed_utc: int,
    actual_mid: float,
    tolerance_seconds: int = 120,
) -> int:
    """Resolve only forecasts whose target just matured.

    If Ramon/MT5 was offline for a while, old targets are deliberately left unresolved
    rather than assigning today's price to a historical target.
    """
    if not db or observed_utc <= 0 or actual_mid <= 0:
        return 0
    path = _ensure_table(db)
    floor = int(observed_utc) - max(15, int(tolerance_seconds))
    with sqlite3.connect(path, timeout=10) as con:
        cur = con.execute(
            """UPDATE chronos_slope_forecasts
               SET actual_price=?, resolved_utc=?, error=raw_forecast-?
               WHERE symbol=? AND model=? AND resolved_utc IS NULL
                 AND target_utc BETWEEN ? AND ?""",
            (
                float(actual_mid), int(observed_utc), float(actual_mid),
                str(symbol), str(model), floor, int(observed_utc),
            ),
        )
        con.commit()
        return int(cur.rowcount or 0)


def record_forecast_steps(
    db: str | Path,
    *,
    symbol: str,
    model: str,
    captured_utc: int,
    source_mid: float,
    steps: list[float],
    bucket_seconds: int = 60,
) -> int:
    """Persist one display-only sample per minute and horizon."""
    if not db or captured_utc <= 0 or source_mid <= 0 or not steps:
        return 0
    path = _ensure_table(db)
    bucket_size = max(15, int(bucket_seconds))
    bucket = int(captured_utc) // bucket_size * bucket_size
    rows = [
        (
            str(symbol), str(model), int(captured_utc), bucket, float(source_mid),
            idx, int(captured_utc) + idx * 900, float(price),
        )
        for idx, price in enumerate(steps, start=1)
        if float(price) > 0
    ]
    with sqlite3.connect(path, timeout=10) as con:
        before = con.total_changes
        con.executemany(
            """INSERT OR IGNORE INTO chronos_slope_forecasts
               (symbol,model,captured_utc,captured_bucket,source_mid,horizon_bars,target_utc,raw_forecast)
               VALUES (?,?,?,?,?,?,?,?)""",
            rows,
        )
        con.commit()
        return int(con.total_changes - before)


def load_horizon_calibrations(
    db: str | Path,
    *,
    symbol: str,
    model: str,
    horizons: int,
    window: int = 96,
    minimum_samples: int = 12,
) -> list[HorizonCalibration]:
    """Return robust per-horizon bias from recent resolved display forecasts.

    Bias is median(raw_forecast - actual_price).  Corrected price is raw - bias.
    Calibration remains inactive until enough samples exist.
    """
    if not db or horizons <= 0:
        return [HorizonCalibration(i) for i in range(1, max(0, horizons) + 1)]
    path = _ensure_table(db)
    result: list[HorizonCalibration] = []
    with sqlite3.connect(path, timeout=10) as con:
        for horizon in range(1, horizons + 1):
            rows = con.execute(
                """SELECT error FROM chronos_slope_forecasts
                   WHERE symbol=? AND model=? AND horizon_bars=?
                     AND resolved_utc IS NOT NULL AND error IS NOT NULL
                   ORDER BY resolved_utc DESC LIMIT ?""",
                (str(symbol), str(model), horizon, max(12, int(window))),
            ).fetchall()
            errors = [float(row[0]) for row in rows]
            samples = len(errors)
            if not errors:
                result.append(HorizonCalibration(horizon))
                continue
            bias = float(statistics.median(errors))
            mae = float(statistics.median(abs(x) for x in errors))
            result.append(
                HorizonCalibration(
                    horizon_bars=horizon,
                    samples=samples,
                    bias=bias,
                    mae=mae,
                    active=samples >= max(1, int(minimum_samples)),
                )
            )
    return result


def apply_calibration(
    raw_steps: list[float],
    calibrations: list[HorizonCalibration],
) -> list[float]:
    corrected: list[float] = []
    for idx, raw in enumerate(raw_steps):
        cal = calibrations[idx] if idx < len(calibrations) else HorizonCalibration(idx + 1)
        corrected.append(float(raw) - cal.bias if cal.active else float(raw))
    return corrected
