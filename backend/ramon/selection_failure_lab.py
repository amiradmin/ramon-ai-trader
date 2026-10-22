from __future__ import annotations

import argparse
import json
import math
import sqlite3
import statistics
from dataclasses import dataclass
from pathlib import Path

from .entry_timing_lab import bootstrap_difference_ci
from .validation_lab import Result, Sample, replay_direction, split_holdout


@dataclass(frozen=True)
class FailureRow:
    signal_bar_time: int
    captured: int
    quote_time: int
    selected: bool
    direction: str
    mid: float
    spread: float
    atr: float
    stop_distance: float
    target_distance: float
    point: float
    features: dict[str, float]


def _json_dict(raw: str | None) -> dict[str, object]:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}
    return value if isinstance(value, dict) else {}


def _finite_number(value: object) -> float | None:
    if isinstance(value, bool):
        return float(int(value))
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _flatten_numeric(prefix: str, value: object, out: dict[str, float]) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {"settings", "shadow_forecasts"}:
                continue
            _flatten_numeric(f"{prefix}.{key}" if prefix else str(key), child, out)
        return
    number = _finite_number(value)
    if number is not None:
        out[prefix] = number


def extract_features(row: sqlite3.Row) -> dict[str, float]:
    features: dict[str, float] = {
        "market.atr": float(row["atr"]),
        "market.spread": float(row["spread"]),
    }
    for label, column in (
        ("regime", "regime_features"),
        ("entry", "entry_features"),
        ("meta", "meta_base_features"),
        ("news", "news_features"),
    ):
        _flatten_numeric(label, _json_dict(row[column]), features)

    metadata = _json_dict(row["model_metadata"])
    base = (
        metadata.get("decision_audit", {}).get("base", {})
        if isinstance(metadata.get("decision_audit"), dict)
        else {}
    )
    if isinstance(base, dict):
        for key in (
            "edge", "buy_edge", "sell_edge", "minimum_edge", "uncertainty",
            "signal_strength", "minimum_strength", "intrabar_confirmed",
            "intrabar_move_atr", "intrabar_rebound_atr", "ai_trend_confirmed",
            "ai_trend_score", "ai_trend_move_atr", "ai_trend_consistency",
            "forecast_low", "forecast_median", "forecast_high",
        ):
            number = _finite_number(base.get(key))
            if number is not None:
                features[f"chronos.{key}"] = number

        direction = str(base.get("decision", "")).upper()
        features["chronos.base_buy"] = 1.0 if direction == "BUY" else 0.0
        features["chronos.base_sell"] = 1.0 if direction == "SELL" else 0.0

    return features


def load_rows(
    db: str | Path,
    symbol: str,
) -> tuple[list[FailureRow], list[sqlite3.Row]]:
    path = Path(db).expanduser()
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    try:
        point_rows = list(
            con.execute(
                """SELECT s.spread,h.spread_points
                   FROM decision_samples s
                   JOIN history_bars h
                     ON h.symbol=s.symbol
                    AND h.timeframe='M15'
                    AND h.time=s.signal_bar_time
                   WHERE s.symbol=? AND s.spread>0 AND h.spread_points>0""",
                (symbol,),
            )
        )
        point_values = [
            float(row["spread"]) / int(row["spread_points"])
            for row in point_rows
            if int(row["spread_points"]) > 0
        ]
        point = statistics.median(point_values) if point_values else 0.0

        candidates = list(
            con.execute(
                """SELECT s.signal_bar_time,s.captured,s.quote_time,s.mid,s.spread,s.atr,
                          s.stop_distance,s.target_distance,s.direction,s.base_decision,
                          s.regime_features,s.entry_features,s.meta_base_features,
                          s.news_features,s.model_metadata,
                          EXISTS(
                              SELECT 1
                              FROM decision_samples sx
                              JOIN trade_outcomes t
                                ON t.sample_key=sx.sample_key
                               AND t.symbol=sx.symbol
                              WHERE sx.symbol=s.symbol
                                AND sx.signal_bar_time=s.signal_bar_time
                          ) AS selected
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
                     AND COALESCE(s.stop_distance,0)>0
                     AND COALESCE(s.target_distance,0)>0
                   ORDER BY s.signal_bar_time""",
                (symbol, symbol),
            )
        )

        rows: list[FailureRow] = []
        for item in candidates:
            direction = str(item["direction"] or item["base_decision"]).upper()
            if direction not in {"BUY", "SELL"}:
                metadata = _json_dict(item["model_metadata"])
                base = (
                    metadata.get("decision_audit", {}).get("base", {})
                    if isinstance(metadata.get("decision_audit"), dict)
                    else {}
                )
                if isinstance(base, dict):
                    buy_edge = _finite_number(base.get("buy_edge"))
                    sell_edge = _finite_number(base.get("sell_edge"))
                    if buy_edge is not None and sell_edge is not None:
                        direction = "BUY" if buy_edge >= sell_edge else "SELL"
            if direction not in {"BUY", "SELL"}:
                continue
            rows.append(
                FailureRow(
                    signal_bar_time=int(item["signal_bar_time"]),
                    captured=int(item["captured"]),
                    quote_time=(
                        int(item["quote_time"])
                        if item["quote_time"] is not None
                        else int(item["signal_bar_time"]) + 900
                    ),
                    selected=bool(item["selected"]),
                    direction=direction,
                    mid=float(item["mid"]),
                    spread=float(item["spread"]),
                    atr=float(item["atr"]),
                    stop_distance=float(item["stop_distance"]),
                    target_distance=float(item["target_distance"]),
                    point=point,
                    features=extract_features(item),
                )
            )

        bars = list(
            con.execute(
                """SELECT time,open,high,low,close,spread_points
                   FROM history_bars
                   WHERE symbol=? AND timeframe='M15'
                   ORDER BY time""",
                (symbol,),
            )
        )
        return rows, bars
    finally:
        con.close()


def as_sample(row: FailureRow) -> Sample:
    return Sample(
        captured=row.captured,
        signal_bar_time=row.signal_bar_time,
        entry_time=row.quote_time,
        entry_time_utc=row.captured,
        direction=row.direction,
        mid=row.mid,
        spread=row.spread,
        stop_distance=row.stop_distance,
        target_distance=row.target_distance,
        point=row.point,
    )


def attach_outcomes(
    rows: list[FailureRow],
    bars: list[sqlite3.Row],
    *,
    max_bars: int,
) -> dict[int, Result]:
    out: dict[int, Result] = {}
    for row in rows:
        result = replay_direction(
            bars, as_sample(row), row.direction, max_bars=max_bars
        )
        if result is not None:
            out[row.signal_bar_time] = result
    return out


def feature_stats(
    rows: list[FailureRow],
    *,
    min_coverage: float,
) -> list[tuple[str, int, int, float, float, float, float]]:
    selected = [row for row in rows if row.selected]
    nonselected = [row for row in rows if not row.selected]
    names = sorted(
        set().union(*(row.features.keys() for row in rows))
        if rows else set()
    )
    stats = []
    for name in names:
        a = [row.features[name] for row in selected if name in row.features]
        b = [row.features[name] for row in nonselected if name in row.features]
        if (
            not a or not b
            or len(a) / max(1, len(selected)) < min_coverage
            or len(b) / max(1, len(nonselected)) < min_coverage
        ):
            continue
        mean_a = statistics.mean(a)
        mean_b = statistics.mean(b)
        pooled_values = a + b
        mean_all = statistics.mean(pooled_values)
        variance = statistics.mean((x - mean_all) ** 2 for x in pooled_values)
        sd = math.sqrt(variance)
        standardized = (mean_a - mean_b) / sd if sd > 1e-12 else 0.0
        stats.append(
            (name, len(a), len(b), mean_a, mean_b, mean_a - mean_b, standardized)
        )
    return sorted(stats, key=lambda item: abs(item[-1]), reverse=True)


def selected_outcome_feature_stats(
    rows: list[FailureRow],
    outcomes: dict[int, Result],
    *,
    min_count: int = 10,
) -> list[tuple[str, int, float, float, float]]:
    selected = [
        row for row in rows
        if row.selected and row.signal_bar_time in outcomes
    ]
    names = sorted(set().union(*(row.features.keys() for row in selected)) if selected else set())
    stats = []
    for name in names:
        pairs = [
            (row.features[name], outcomes[row.signal_bar_time].outcome_r)
            for row in selected
            if name in row.features
        ]
        if len(pairs) < min_count:
            continue
        values = [x for x, _ in pairs]
        rs = [r for _, r in pairs]
        mean_x = statistics.mean(values)
        mean_r = statistics.mean(rs)
        cov = statistics.mean(
            (x - mean_x) * (r - mean_r) for x, r in pairs
        )
        var_x = statistics.mean((x - mean_x) ** 2 for x in values)
        var_r = statistics.mean((r - mean_r) ** 2 for r in rs)
        corr = (
            cov / math.sqrt(var_x * var_r)
            if var_x > 1e-12 and var_r > 1e-12 else 0.0
        )

        ordered = sorted(pairs, key=lambda item: item[0])
        q = max(1, len(ordered) // 4)
        low_mean_r = statistics.mean(r for _, r in ordered[:q])
        high_mean_r = statistics.mean(r for _, r in ordered[-q:])
        stats.append((name, len(pairs), corr, low_mean_r, high_mean_r))
    return sorted(stats, key=lambda item: abs(item[2]), reverse=True)


def print_feature_shift(
    title: str,
    rows: list[FailureRow],
    *,
    min_coverage: float,
    top: int,
) -> None:
    selected = sum(row.selected for row in rows)
    print(title)
    print(f"bars={len(rows)} selected={selected} nonselected={len(rows)-selected}")
    print("feature                               n_sel n_non   sel_mean   non_mean      diff    stdΔ")
    print("-------------------------------------------------------------------------------------------")
    for name, na, nb, ma, mb, diff, standardized in feature_stats(
        rows, min_coverage=min_coverage
    )[:top]:
        print(
            f"{name[:36]:36s} {na:5d} {nb:5d} "
            f"{ma:10.4f} {mb:10.4f} {diff:9.4f} {standardized:7.3f}"
        )
    print()


def print_selected_outcome_relation(
    title: str,
    rows: list[FailureRow],
    outcomes: dict[int, Result],
    *,
    top: int,
) -> None:
    print(title)
    print("feature                                  n    corrR    lowQ_R   highQ_R")
    print("-----------------------------------------------------------------------")
    for name, n, corr, low_r, high_r in selected_outcome_feature_stats(
        rows, outcomes
    )[:top]:
        print(
            f"{name[:40]:40s} {n:4d} "
            f"{corr:8.3f} {low_r:9.4f} {high_r:9.4f}"
        )
    print()


def run(
    db: str | Path,
    symbol: str,
    *,
    max_bars: int,
    holdout: float,
    top: int,
    min_coverage: float,
) -> None:
    rows, bars = load_rows(db, symbol)
    samples = [
        Sample(
            captured=row.captured,
            signal_bar_time=row.signal_bar_time,
            entry_time=row.quote_time,
            entry_time_utc=row.captured,
            direction=row.direction,
            mid=row.mid,
            spread=row.spread,
            stop_distance=row.stop_distance,
            target_distance=row.target_distance,
            point=row.point,
        )
        for row in rows
    ]
    _dev_samples, test_samples = split_holdout(samples, holdout)
    test_keys = {sample.signal_bar_time for sample in test_samples}
    test_rows = [row for row in rows if row.signal_bar_time in test_keys]

    full_outcomes = attach_outcomes(rows, bars, max_bars=max_bars)
    test_outcomes = attach_outcomes(test_rows, bars, max_bars=max_bars)

    print("=== RAMON SELECTION FAILURE LAB ===")
    print(f"Symbol                  : {symbol}")
    print(f"Unique signal bars      : {len(rows)}")
    print(f"Final holdout bars      : {len(test_rows)}")
    print(f"Replay horizon          : {max_bars} M15 bars")
    print("Snapshot policy         : first persisted snapshot per signal_bar_time")
    print("Analysis                : selected vs nonselected + selected outcome relation")
    print("Live effect             : NONE (research-only)")
    print()

    print_feature_shift(
        "=== FULL HISTORY / SELECTION FEATURE SHIFT ===",
        rows,
        min_coverage=min_coverage,
        top=top,
    )
    print_feature_shift(
        "=== FINAL HOLDOUT / SELECTION FEATURE SHIFT ===",
        test_rows,
        min_coverage=min_coverage,
        top=top,
    )
    print_selected_outcome_relation(
        "=== FULL HISTORY / SELECTED FEATURES VS REPLAY R ===",
        rows,
        full_outcomes,
        top=top,
    )
    print_selected_outcome_relation(
        "=== FINAL HOLDOUT / SELECTED FEATURES VS REPLAY R ===",
        test_rows,
        test_outcomes,
        top=top,
    )

    print(
        "Interpretation           : large |stdΔ| identifies features that distinguish "
        "selected bars; corrR and low/high quartile R show whether higher feature values "
        "were associated with better or worse selected-bar replay. This is diagnostic, "
        "not evidence to change a live threshold by itself."
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Research-only Ramon selection failure diagnostic"
    )
    parser.add_argument("--db", default="/data/ramon_history.sqlite3")
    parser.add_argument("--symbol", default="XAUUSD_l")
    parser.add_argument("--max-bars", type=int, default=24)
    parser.add_argument("--holdout", type=float, default=0.30)
    parser.add_argument("--top", type=int, default=20)
    parser.add_argument("--min-coverage", type=float, default=0.70)
    args = parser.parse_args()

    if args.max_bars < 1:
        parser.error("--max-bars must be >= 1")
    if not 0.10 <= args.holdout <= 0.50:
        parser.error("--holdout must be between 0.10 and 0.50")
    if args.top < 5:
        parser.error("--top must be >= 5")
    if not 0.0 < args.min_coverage <= 1.0:
        parser.error("--min-coverage must be in (0, 1]")

    run(
        args.db,
        args.symbol,
        max_bars=args.max_bars,
        holdout=args.holdout,
        top=args.top,
        min_coverage=args.min_coverage,
    )


if __name__ == "__main__":
    main()
