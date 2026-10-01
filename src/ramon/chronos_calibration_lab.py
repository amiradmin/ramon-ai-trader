from __future__ import annotations

import argparse
import json
import math
import sqlite3
import statistics
from dataclasses import dataclass
from pathlib import Path


REGIME_FEATURE_NAMES = (
    "ret_1_atr",
    "ret_4_atr",
    "ret_12_atr",
    "range_12_atr",
    "body_efficiency_12",
)


@dataclass(frozen=True)
class CalibrationRow:
    signal_bar_time: int
    bar_index: int
    target_index: int
    close: float
    atr: float
    raw_forecast: float
    raw_move_atr: float
    target_close: float
    target_move_atr: float
    regime: tuple[float, ...]


@dataclass(frozen=True)
class Fold:
    number: int
    development: tuple[CalibrationRow, ...]
    test: tuple[CalibrationRow, ...]


@dataclass(frozen=True)
class Standardizer:
    means: tuple[float, ...]
    scales: tuple[float, ...]

    def transform(self, values: tuple[float, ...]) -> tuple[float, ...]:
        return tuple(
            (value - mean) / scale
            for value, mean, scale in zip(values, self.means, self.scales)
        )


@dataclass(frozen=True)
class LinearModel:
    intercept: float
    weights: tuple[float, ...]
    standardizer: Standardizer

    def predict(self, values: tuple[float, ...]) -> float:
        x = self.standardizer.transform(values)
        return self.intercept + sum(w * v for w, v in zip(self.weights, x))


def _isfinite_positive(value: object) -> bool:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(number) and number > 0


def _extract_forecast(raw: str | None) -> float | None:
    if not raw:
        return None
    try:
        data = json.loads(raw)
        value = data["decision_audit"]["base"]["forecast_median"]
    except (json.JSONDecodeError, TypeError, KeyError):
        return None
    return float(value) if _isfinite_positive(value) else None


def _extract_regime(raw: str | None) -> tuple[float, ...] | None:
    if not raw:
        return None
    try:
        data = json.loads(raw)
        values = tuple(float(data[name]) for name in REGIME_FEATURE_NAMES)
    except (json.JSONDecodeError, TypeError, KeyError, ValueError):
        return None
    return values if all(math.isfinite(value) for value in values) else None


def load_rows(
    db: str | Path,
    symbol: str,
    *,
    horizon: int,
) -> tuple[list[CalibrationRow], int, int]:
    """Load one immutable Chronos snapshot per completed M15 bar.

    The first snapshot for each signal_bar_time is used to avoid overweighting
    the same Chronos forecast through Ramon's 30-second snapshot cadence.
    The realized target is the close of the Nth *observed* future M15 bar, so
    weekends/gaps do not turn a four-step horizon into wall-clock arithmetic.
    """
    path = Path(db).expanduser()
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    try:
        bars = list(
            con.execute(
                """SELECT time,close
                   FROM history_bars
                   WHERE symbol=? AND timeframe='M15'
                   ORDER BY time""",
                (symbol,),
            )
        )
        bar_index = {int(row["time"]): index for index, row in enumerate(bars)}

        samples = list(
            con.execute(
                """SELECT s.signal_bar_time,s.captured,s.atr,
                          s.regime_features,s.model_metadata
                   FROM decision_samples s
                   JOIN (
                       SELECT signal_bar_time,MIN(captured) AS first_captured
                       FROM decision_samples
                       WHERE symbol=?
                       GROUP BY signal_bar_time
                   ) first
                     ON first.signal_bar_time=s.signal_bar_time
                    AND first.first_captured=s.captured
                   WHERE s.symbol=?
                     AND s.atr>0
                     AND s.model_metadata IS NOT NULL
                   ORDER BY s.signal_bar_time""",
                (symbol, symbol),
            )
        )

        rows: list[CalibrationRow] = []
        forecast_available = 0
        for sample in samples:
            forecast = _extract_forecast(sample["model_metadata"])
            if forecast is None:
                continue
            forecast_available += 1
            regime = _extract_regime(sample["regime_features"])
            if regime is None:
                continue
            signal_time = int(sample["signal_bar_time"])
            index = bar_index.get(signal_time)
            if index is None or index + horizon >= len(bars):
                continue
            close = float(bars[index]["close"])
            target_close = float(bars[index + horizon]["close"])
            atr = float(sample["atr"])
            if not all(math.isfinite(v) and v > 0 for v in (close, target_close, atr)):
                continue
            rows.append(
                CalibrationRow(
                    signal_bar_time=signal_time,
                    bar_index=index,
                    target_index=index + horizon,
                    close=close,
                    atr=atr,
                    raw_forecast=forecast,
                    raw_move_atr=(forecast - close) / atr,
                    target_close=target_close,
                    target_move_atr=(target_close - close) / atr,
                    regime=regime,
                )
            )
        return rows, len(samples), forecast_available
    finally:
        con.close()


def walk_forward_folds(
    rows: list[CalibrationRow],
    *,
    folds: int,
    horizon: int,
    initial_fraction: float = 0.40,
) -> list[Fold]:
    if folds < 2 or len(rows) < folds + 10:
        return []
    ordered = sorted(rows, key=lambda row: row.bar_index)
    initial = max(20, min(len(ordered) - folds, int(len(ordered) * initial_fraction)))
    remaining = len(ordered) - initial
    base = remaining // folds
    extra = remaining % folds
    if base <= 0:
        return []

    result: list[Fold] = []
    start = initial
    for number in range(1, folds + 1):
        size = base + (1 if number <= extra else 0)
        test = ordered[start:start + size]
        if not test:
            break
        test_start_index = test[0].bar_index
        development = tuple(
            row
            for row in ordered[:start]
            if row.target_index < test_start_index
        )
        if len(development) >= max(20, horizon * 4):
            result.append(Fold(number, development, tuple(test)))
        start += size
    return result


def _fit_standardizer(rows: list[tuple[float, ...]]) -> Standardizer:
    columns = list(zip(*rows))
    means = tuple(statistics.mean(column) for column in columns)
    scales = []
    for column, mean in zip(columns, means):
        variance = statistics.mean((value - mean) ** 2 for value in column)
        scales.append(max(math.sqrt(variance), 1e-9))
    return Standardizer(means, tuple(scales))


def _solve_linear(matrix: list[list[float]], vector: list[float]) -> list[float]:
    n = len(vector)
    aug = [row[:] + [vector[i]] for i, row in enumerate(matrix)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(aug[r][col]))
        if abs(aug[pivot][col]) < 1e-12:
            aug[pivot][col] = 1e-12
        aug[col], aug[pivot] = aug[pivot], aug[col]
        divisor = aug[col][col]
        aug[col] = [value / divisor for value in aug[col]]
        for row in range(n):
            if row == col:
                continue
            factor = aug[row][col]
            if factor == 0:
                continue
            aug[row] = [
                a - factor * b for a, b in zip(aug[row], aug[col])
            ]
    return [aug[i][-1] for i in range(n)]


def fit_ridge(
    features: list[tuple[float, ...]],
    targets: list[float],
    *,
    l2: float,
) -> LinearModel:
    if len(features) != len(targets) or len(features) < 10:
        raise ValueError("need at least 10 aligned calibration rows")
    width = len(features[0])
    if width < 1 or any(len(row) != width for row in features):
        raise ValueError("invalid feature width")
    standardizer = _fit_standardizer(features)
    x_rows = [(1.0, *standardizer.transform(row)) for row in features]
    p = width + 1
    xtx = [[0.0] * p for _ in range(p)]
    xty = [0.0] * p
    for x, y in zip(x_rows, targets):
        for i in range(p):
            xty[i] += x[i] * y
            for j in range(p):
                xtx[i][j] += x[i] * x[j]
    for i in range(1, p):
        xtx[i][i] += l2
    beta = _solve_linear(xtx, xty)
    return LinearModel(beta[0], tuple(beta[1:]), standardizer)


def _features_linear(row: CalibrationRow) -> tuple[float, ...]:
    return (row.raw_move_atr,)


def _features_regime(row: CalibrationRow) -> tuple[float, ...]:
    return (row.raw_move_atr, *row.regime)


def _sign(value: float) -> int:
    if value > 0:
        return 1
    if value < 0:
        return -1
    return 0


def metrics(
    rows: list[CalibrationRow],
    predictions: list[float],
) -> dict[str, float]:
    if len(rows) != len(predictions) or not rows:
        return {
            "n": 0.0,
            "mae_atr": math.nan,
            "rmse_atr": math.nan,
            "bias_atr": math.nan,
            "direction_acc": math.nan,
        }
    errors = [pred - row.target_move_atr for row, pred in zip(rows, predictions)]
    directional = [
        int(_sign(pred) == _sign(row.target_move_atr))
        for row, pred in zip(rows, predictions)
        if _sign(row.target_move_atr) != 0
    ]
    return {
        "n": float(len(rows)),
        "mae_atr": statistics.mean(abs(error) for error in errors),
        "rmse_atr": math.sqrt(statistics.mean(error * error for error in errors)),
        "bias_atr": statistics.mean(errors),
        "direction_acc": (
            100.0 * sum(directional) / len(directional) if directional else math.nan
        ),
    }


def evaluate_fold(
    development: list[CalibrationRow],
    test: list[CalibrationRow],
    *,
    l2: float,
) -> dict[str, dict[str, float]]:
    raw_predictions = [row.raw_move_atr for row in test]

    mean_error = statistics.mean(
        row.raw_move_atr - row.target_move_atr for row in development
    )
    bias_predictions = [row.raw_move_atr - mean_error for row in test]

    linear = fit_ridge(
        [_features_linear(row) for row in development],
        [row.target_move_atr for row in development],
        l2=l2,
    )
    linear_predictions = [linear.predict(_features_linear(row)) for row in test]

    regime = fit_ridge(
        [_features_regime(row) for row in development],
        [row.target_move_atr for row in development],
        l2=l2,
    )
    regime_predictions = [regime.predict(_features_regime(row)) for row in test]

    return {
        "raw": metrics(test, raw_predictions),
        "bias_corrected": metrics(test, bias_predictions),
        "linear": metrics(test, linear_predictions),
        "regime_aware": metrics(test, regime_predictions),
    }


def run(
    db: str | Path,
    symbol: str,
    *,
    horizon: int,
    folds: int,
    l2: float,
) -> None:
    rows, unique_signal_bars, forecast_available = load_rows(
        db, symbol, horizon=horizon
    )
    wf = walk_forward_folds(rows, folds=folds, horizon=horizon)

    print("=== RAMON CHRONOS CALIBRATION LAB ===")
    print(f"Symbol                  : {symbol}")
    print(f"Forecast horizon        : {horizon} M15 bars")
    print(f"Unique signal bars      : {unique_signal_bars}")
    print(f"Forecast metadata       : {forecast_available}/{unique_signal_bars}")
    print(f"Usable labeled bars     : {len(rows)}")
    print("Deduplication           : first snapshot per signal_bar_time")
    print("Target                  : close of Nth observed future M15 bar")
    print("Prediction units        : future move / entry ATR")
    print("Live effect             : NONE (research-only)")
    print()

    if not wf:
        print("Not enough labeled bars for requested walk-forward configuration.")
        return

    names = ("raw", "bias_corrected", "linear", "regime_aware")
    aggregate: dict[str, list[tuple[CalibrationRow, float]]] = {
        name: [] for name in names
    }

    print("=== PURGED WALK-FORWARD FORECAST CALIBRATION ===")
    print(
        "fold dev test | raw MAE/Dir | bias MAE/Dir | linear MAE/Dir | regime MAE/Dir"
    )
    print("-" * 88)

    for fold in wf:
        development = list(fold.development)
        test = list(fold.test)

        mean_error = statistics.mean(
            row.raw_move_atr - row.target_move_atr for row in development
        )
        linear_model = fit_ridge(
            [_features_linear(row) for row in development],
            [row.target_move_atr for row in development],
            l2=l2,
        )
        regime_model = fit_ridge(
            [_features_regime(row) for row in development],
            [row.target_move_atr for row in development],
            l2=l2,
        )

        predictions = {
            "raw": [row.raw_move_atr for row in test],
            "bias_corrected": [row.raw_move_atr - mean_error for row in test],
            "linear": [
                linear_model.predict(_features_linear(row)) for row in test
            ],
            "regime_aware": [
                regime_model.predict(_features_regime(row)) for row in test
            ],
        }
        fold_metrics = {
            name: metrics(test, values) for name, values in predictions.items()
        }

        for name in names:
            aggregate[name].extend(zip(test, predictions[name]))

        def cell(name: str) -> str:
            value = fold_metrics[name]
            return (
                f"{value['mae_atr']:.3f}/"
                f"{value['direction_acc']:.1f}%"
            )

        print(
            f"{fold.number:>4d} {len(development):>3d} {len(test):>4d} | "
            f"{cell('raw'):>11s} | {cell('bias_corrected'):>12s} | "
            f"{cell('linear'):>14s} | {cell('regime_aware'):>14s}"
        )

    print()
    print("=== AGGREGATE WALK-FORWARD OOS ===")
    print("model             n    MAE_ATR  RMSE_ATR  Bias_ATR  DirAcc%")
    print("------------------------------------------------------------")
    aggregate_metrics: dict[str, dict[str, float]] = {}
    for name in names:
        pairs = aggregate[name]
        test_rows = [row for row, _ in pairs]
        preds = [pred for _, pred in pairs]
        m = metrics(test_rows, preds)
        aggregate_metrics[name] = m
        print(
            f"{name:16s} {int(m['n']):4d} "
            f"{m['mae_atr']:9.4f} {m['rmse_atr']:9.4f} "
            f"{m['bias_atr']:9.4f} {m['direction_acc']:8.2f}"
        )

    raw = aggregate_metrics["raw"]
    for name in ("bias_corrected", "linear", "regime_aware"):
        calibrated = aggregate_metrics[name]
        mae_change = 100.0 * (raw["mae_atr"] - calibrated["mae_atr"]) / raw["mae_atr"]
        dir_change = calibrated["direction_acc"] - raw["direction_acc"]
        print(
            f"{name:16s}: MAE improvement vs raw={mae_change:+.2f}% | "
            f"direction Δ={dir_change:+.2f} pp"
        )

    print()
    print(
        "Interpretation           : calibration must improve OOS error and/or "
        "direction consistently before any shadow deployment."
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Research-only Chronos forecast calibration lab"
    )
    parser.add_argument("--db", default="/data/ramon_history.sqlite3")
    parser.add_argument("--symbol", default="XAUUSD_l")
    parser.add_argument("--horizon", type=int, default=4)
    parser.add_argument("--folds", type=int, default=5)
    parser.add_argument("--l2", type=float, default=1.0)
    args = parser.parse_args()

    if args.horizon < 1 or args.horizon > 24:
        parser.error("--horizon must be between 1 and 24")
    if args.folds < 2:
        parser.error("--folds must be >= 2")
    if not math.isfinite(args.l2) or args.l2 < 0:
        parser.error("--l2 must be finite and >= 0")

    run(
        args.db,
        args.symbol,
        horizon=args.horizon,
        folds=args.folds,
        l2=args.l2,
    )


if __name__ == "__main__":
    main()
