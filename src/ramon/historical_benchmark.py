"""Ramon M15 historical benchmark lab for external OHLC datasets.

This module is deliberately isolated from the live EA. It compares simple,
non-trained reference forecasters on identical chronological windows and reports
trade-level metrics. External datasets with unknown spread use an explicit
fallback spread supplied by the caller.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
import json
from math import sqrt
import sqlite3
import struct
from pathlib import Path
from statistics import mean, pstdev
from typing import Sequence

from .compare import MomentumBaseline
from .core import Bar, Forecast, Market, Settings, evaluate
from .history import load_bars
from .market_state import assess_market
from .progress import ProgressReporter, ProgressSlice


class PreviousBarBaseline:
    """Project the most recent completed-bar move forward; no fitting or future data."""

    def forecast(self, closes: Sequence[float], horizon: int) -> Forecast:
        if len(closes) < 16 or horizon < 1:
            raise ValueError("need >=16 historical closes and a positive horizon")
        move = closes[-1] - closes[-2]
        changes = [b - a for a, b in zip(closes[-16:-1], closes[-15:])]
        width = max(pstdev(changes) * sqrt(horizon) * 1.28, 1e-6)
        path = tuple(closes[-1] + move * n for n in range(1, horizon + 1))
        median = path[-1]
        return Forecast(max(1e-6, median - width), median, median + width, path)


class ContrarianBaseline:
    """Mirror another forecaster around the latest completed close."""

    def __init__(self, base) -> None:
        self.base = base

    def forecast(self, closes: Sequence[float], horizon: int) -> Forecast:
        base = self.base.forecast(closes, horizon)
        anchor = closes[-1]
        path = tuple(max(1e-6, anchor - (value - anchor)) for value in base.median_path)
        median = max(1e-6, anchor - (base.median - anchor))
        # Mirror the interval and keep every forecast value strictly positive.
        low = max(1e-6, anchor - (base.high - anchor))
        high = max(low, anchor - (base.low - anchor))
        median = min(max(median, low), high)
        return Forecast(low, median, high, path)


class PersistentForecastCache:
    """Persistent research cache so expensive model forecasts can resume safely."""

    def __init__(self, model, db: str | Path, identity: str) -> None:
        self.model = model
        self.identity = identity
        self.path = Path(db).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("""CREATE TABLE IF NOT EXISTS forecast_cache (
            cache_key TEXT PRIMARY KEY,
            model_identity TEXT NOT NULL,
            horizon INTEGER NOT NULL,
            low REAL NOT NULL,
            median REAL NOT NULL,
            high REAL NOT NULL,
            median_path TEXT NOT NULL
        )""")
        self.pending = 0
        self.hits = 0
        self.misses = 0

    def _key(self, closes: Sequence[float], horizon: int) -> str:
        digest = hashlib.sha256()
        digest.update(self.identity.encode("utf-8") + b"\0")
        digest.update(str(horizon).encode("ascii") + b"\0")
        for value in closes:
            digest.update(struct.pack("!d", float(value)))
        return digest.hexdigest()

    def forecast(self, closes: Sequence[float], horizon: int) -> Forecast:
        key = self._key(closes, horizon)
        row = self.conn.execute(
            "SELECT low,median,high,median_path FROM forecast_cache WHERE cache_key=?",
            (key,),
        ).fetchone()
        if row:
            self.hits += 1
            return Forecast(float(row[0]), float(row[1]), float(row[2]),
                            tuple(float(v) for v in json.loads(row[3])))
        forecast = self.model.forecast(closes, horizon)
        self.conn.execute(
            """INSERT OR REPLACE INTO forecast_cache
               (cache_key,model_identity,horizon,low,median,high,median_path)
               VALUES (?,?,?,?,?,?,?)""",
            (key, self.identity, horizon, forecast.low, forecast.median, forecast.high,
             json.dumps(list(forecast.median_path), separators=(",", ":"))),
        )
        self.misses += 1
        self.pending += 1
        if self.pending >= 100:
            self.conn.commit()
            self.pending = 0
        return forecast

    def stats(self) -> dict[str, object]:
        return {
            "path": str(self.path),
            "identity": self.identity,
            "hits": self.hits,
            "misses": self.misses,
        }

    def close(self) -> None:
        self.conn.commit()
        self.conn.close()


@dataclass(frozen=True, slots=True)
class TradeRecord:
    signal_time: int
    direction: str
    outcome: str
    r: float
    regime: str


def _metrics(trades: Sequence[TradeRecord]) -> dict[str, object]:
    wins = sum(t.outcome == "WIN" for t in trades)
    losses = sum(t.outcome == "LOSS" for t in trades)
    timed_out = sum(t.outcome == "TIMEOUT" for t in trades)
    gross_profit = sum(max(t.r, 0.0) for t in trades)
    gross_loss = -sum(min(t.r, 0.0) for t in trades)
    net = sum(t.r for t in trades)
    equity = peak = drawdown = 0.0
    for trade in trades:
        equity += trade.r
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    resolved = wins + losses
    return {
        "trades": len(trades),
        "buys": sum(t.direction == "BUY" for t in trades),
        "sells": sum(t.direction == "SELL" for t in trades),
        "wins": wins,
        "losses": losses,
        "timed_out": timed_out,
        "resolved_win_rate": round(wins / resolved, 6) if resolved else None,
        "timeout_rate": round(timed_out / len(trades), 6) if trades else None,
        "profit_factor": round(gross_profit / gross_loss, 6) if gross_loss > 0 else None,
        "gross_profit_r": round(gross_profit, 4),
        "gross_loss_r": round(gross_loss, 4),
        "mean_r": round(net / len(trades), 6) if trades else None,
        "net_r": round(net, 4),
        "max_drawdown_r": round(drawdown, 4),
    }


def _window_iso(bars: Sequence[Bar], start: int, end: int) -> dict[str, object]:
    return {
        "start_index": start,
        "end_index_exclusive": end,
        "start_utc": datetime.fromtimestamp(bars[start].time, timezone.utc).isoformat(),
        "end_utc": datetime.fromtimestamp(bars[end - 1].time, timezone.utc).isoformat(),
        "bars": end - start,
    }


def simulate(
    bars: Sequence[Bar],
    spreads: Sequence[int],
    model,
    *,
    symbol: str,
    point: float,
    settings: Settings,
    start: int,
    end: int,
    stride: int,
    fallback_spread_points: int,
    roundtrip_cost_r: float,
    progress: ProgressSlice | None = None,
    progress_stage: str = "replay",
) -> tuple[list[TradeRecord], dict[str, int]]:
    """Replay one non-overlapping position stream and preserve per-trade R."""
    if len(bars) != len(spreads) or stride < 1 or point <= 0 or fallback_spread_points <= 0:
        raise ValueError("invalid benchmark input")
    if roundtrip_cost_r < 0 or not 0 <= start < end <= len(bars):
        raise ValueError("invalid benchmark window")

    start_at = max(256, start)
    i = start_at
    decisions = waits = 0
    trades: list[TradeRecord] = []
    span = max(1, end - settings.horizon - start_at)
    local_progress = (
        ProgressSlice(progress.parent, progress.start, progress.end, span, progress_stage)
        if progress is not None else None
    )
    while i + settings.horizon < end:
        if local_progress is not None:
            local_progress.update(i - start_at, stage=progress_stage)
        if (i - start_at) % stride:
            i += 1
            continue
        spread_points = spreads[i] if spreads[i] > 0 else fallback_spread_points
        spread = spread_points * point
        market = Market(
            symbol=symbol,
            timeframe="M15",
            bid=bars[i].close,
            ask=bars[i].close + spread,
            point=point,
            bars=tuple(bars[max(0, i - 255): i + 1]),
        )
        regime = assess_market(market)["state"]
        result = evaluate(market, model, settings)
        decisions += 1
        if result.decision == "WAIT":
            waits += 1
            i += 1
            continue

        entry_spread_points = spreads[i + 1] if spreads[i + 1] > 0 else fallback_spread_points
        entry_spread = entry_spread_points * point
        entry = bars[i + 1].open + (entry_spread if result.decision == "BUY" else 0.0)
        stop = entry - result.stop_distance if result.decision == "BUY" else entry + result.stop_distance
        target = entry + result.target_distance if result.decision == "BUY" else entry - result.target_distance
        closed_at = i + settings.horizon
        outcome = "TIMEOUT"
        trade_r = 0.0

        for j in range(i + 1, closed_at + 1):
            bar = bars[j]
            ask_spread_points = spreads[j] if spreads[j] > 0 else fallback_spread_points
            ask_spread = ask_spread_points * point
            if result.decision == "BUY":
                stop_hit = bar.low <= stop
                target_hit = bar.high >= target
            else:
                stop_hit = bar.high + ask_spread >= stop
                target_hit = bar.low + ask_spread <= target
            if stop_hit or target_hit:
                outcome = "LOSS" if stop_hit else "WIN"  # conservative unknown intrabar ordering
                trade_r = -1.0 if stop_hit else result.target_distance / result.stop_distance
                closed_at = j
                break

        if outcome == "TIMEOUT":
            exit_price = bars[closed_at].close
            exit_spread_points = spreads[closed_at] if spreads[closed_at] > 0 else fallback_spread_points
            exit_ask = exit_price + exit_spread_points * point
            delta = exit_price - entry if result.decision == "BUY" else entry - exit_ask
            trade_r = delta / result.stop_distance

        trade_r -= roundtrip_cost_r
        trades.append(TradeRecord(bars[i].time, result.decision, outcome, trade_r, regime))
        i = closed_at + 1

    if local_progress is not None:
        local_progress.finish(stage=progress_stage)
    return trades, {"decisions": decisions, "waits": waits}


def _by_year(trades: Sequence[TradeRecord]) -> dict[str, dict[str, object]]:
    grouped: dict[int, list[TradeRecord]] = defaultdict(list)
    for trade in trades:
        grouped[datetime.fromtimestamp(trade.signal_time, timezone.utc).year].append(trade)
    return {str(year): _metrics(grouped[year]) for year in sorted(grouped)}


def _direction_metrics(trades: Sequence[TradeRecord]) -> dict[str, dict[str, object]]:
    return {
        direction: _metrics([t for t in trades if t.direction == direction])
        for direction in ("BUY", "SELL")
    }


def _regime_metrics(trades: Sequence[TradeRecord]) -> dict[str, dict[str, object]]:
    grouped: dict[str, list[TradeRecord]] = defaultdict(list)
    for trade in trades:
        grouped[trade.regime].append(trade)
    return {state: _metrics(grouped[state]) for state in sorted(grouped)}



def directional_accuracy(
    bars: Sequence[Bar],
    model,
    *,
    start: int,
    end: int,
    horizons: Sequence[int],
    stride: int,
    progress: ProgressSlice | None = None,
    progress_stage: str = "directional accuracy",
) -> dict[str, dict[str, object]]:
    """Measure raw forecast direction against future closes, without trade exits."""
    if stride < 1:
        raise ValueError("stride must be positive")
    out: dict[str, dict[str, object]] = {}
    ranges = [
        range(max(256, start), end - horizon, stride)
        for horizon in horizons
    ]
    total_points = max(1, sum(len(rng) for rng in ranges))
    local_progress = (
        ProgressSlice(progress.parent, progress.start, progress.end, total_points, progress_stage)
        if progress is not None else None
    )
    done_points = 0
    for horizon, indices in zip(horizons, ranges):
        if horizon < 1:
            raise ValueError("directional horizons must be positive")
        correct = wrong = flat_forecast = flat_actual = 0
        signed_moves: list[float] = []
        for i in indices:
            if local_progress is not None:
                local_progress.update(done_points, stage=f"{progress_stage} H={horizon}")
            done_points += 1
            closes = [bar.close for bar in bars[max(0, i - 255):i + 1]]
            forecast = model.forecast(closes, horizon)
            predicted_move = forecast.median - bars[i].close
            actual_move = bars[i + horizon].close - bars[i].close
            if predicted_move == 0:
                flat_forecast += 1
                continue
            if actual_move == 0:
                flat_actual += 1
                continue
            predicted_sign = 1 if predicted_move > 0 else -1
            actual_sign = 1 if actual_move > 0 else -1
            signed_moves.append(actual_move * predicted_sign)
            if predicted_sign == actual_sign:
                correct += 1
            else:
                wrong += 1
        resolved = correct + wrong
        out[str(horizon)] = {
            "samples": resolved,
            "correct": correct,
            "wrong": wrong,
            "flat_forecast": flat_forecast,
            "flat_actual": flat_actual,
            "accuracy": round(correct / resolved, 6) if resolved else None,
            "mean_signed_move": round(mean(signed_moves), 6) if signed_moves else None,
        }
    if local_progress is not None:
        local_progress.finish(stage=progress_stage)
    return out


def regime_directional_accuracy(
    bars: Sequence[Bar],
    spreads: Sequence[int],
    model,
    *,
    symbol: str,
    point: float,
    start: int,
    end: int,
    horizons: Sequence[int],
    stride: int,
    fallback_spread_points: int,
    progress: ProgressSlice | None = None,
    progress_stage: str = "regime directional",
) -> dict[str, dict[str, dict[str, object]]]:
    """Raw forecast direction grouped by Ramon's price-only market state."""
    if stride < 1 or point <= 0 or fallback_spread_points <= 0:
        raise ValueError("invalid regime directional input")
    out: dict[str, dict[str, dict[str, object]]] = {}
    ranges = [
        range(max(256, start), end - horizon, stride)
        for horizon in horizons
    ]
    total_points = max(1, sum(len(rng) for rng in ranges))
    local_progress = (
        ProgressSlice(progress.parent, progress.start, progress.end, total_points, progress_stage)
        if progress is not None else None
    )
    done_points = 0
    for horizon, indices in zip(horizons, ranges):
        grouped: dict[str, dict[str, object]] = defaultdict(
            lambda: {"correct": 0, "wrong": 0, "flat_forecast": 0, "flat_actual": 0, "signed_moves": []}
        )
        for i in indices:
            if local_progress is not None:
                local_progress.update(done_points, stage=f"{progress_stage} H={horizon}")
            done_points += 1
            spread_points = spreads[i] if spreads[i] > 0 else fallback_spread_points
            market = Market(
                symbol=symbol,
                timeframe="M15",
                bid=bars[i].close,
                ask=bars[i].close + spread_points * point,
                point=point,
                bars=tuple(bars[max(0, i - 255): i + 1]),
            )
            state = assess_market(market)["state"]
            closes = [bar.close for bar in market.bars]
            forecast = model.forecast(closes, horizon)
            predicted_move = forecast.median - bars[i].close
            actual_move = bars[i + horizon].close - bars[i].close
            bucket = grouped[state]
            if predicted_move == 0:
                bucket["flat_forecast"] += 1
                continue
            if actual_move == 0:
                bucket["flat_actual"] += 1
                continue
            predicted_sign = 1 if predicted_move > 0 else -1
            actual_sign = 1 if actual_move > 0 else -1
            bucket["signed_moves"].append(actual_move * predicted_sign)
            if predicted_sign == actual_sign:
                bucket["correct"] += 1
            else:
                bucket["wrong"] += 1

        horizon_report: dict[str, dict[str, object]] = {}
        for state, values in sorted(grouped.items()):
            resolved = int(values["correct"]) + int(values["wrong"])
            moves = values["signed_moves"]
            horizon_report[state] = {
                "samples": resolved,
                "correct": values["correct"],
                "wrong": values["wrong"],
                "flat_forecast": values["flat_forecast"],
                "flat_actual": values["flat_actual"],
                "accuracy": round(int(values["correct"]) / resolved, 6) if resolved else None,
                "mean_signed_move": round(mean(moves), 6) if moves else None,
            }
        out[str(horizon)] = horizon_report
    if local_progress is not None:
        local_progress.finish(stage=progress_stage)
    return out


def horizon_matrix(
    bars: Sequence[Bar],
    spreads: Sequence[int],
    model,
    *,
    symbol: str,
    point: float,
    base_settings: Settings,
    start: int,
    end: int,
    horizons: Sequence[int],
    stride: int,
    fallback_spread_points: int,
    roundtrip_cost_r: float,
    progress: ProgressSlice | None = None,
    progress_stage: str = "horizon matrix",
) -> dict[str, dict[str, object]]:
    """Replay identical signal model with several holding horizons."""
    report: dict[str, dict[str, object]] = {}
    count = max(1, len(horizons))
    for pos, horizon in enumerate(horizons):
        settings = replace(base_settings, horizon=horizon)
        child = None
        if progress is not None:
            child = ProgressSlice(progress.parent,
                                  progress.start + (progress.end-progress.start)*pos//count,
                                  progress.start + (progress.end-progress.start)*(pos+1)//count,
                                  max(1, end-start),
                                  f"{progress_stage} H={horizon}")
        trades, counters = simulate(
            bars, spreads, model, symbol=symbol, point=point, settings=settings,
            start=start, end=end, stride=stride,
            fallback_spread_points=fallback_spread_points,
            roundtrip_cost_r=roundtrip_cost_r,
            progress=child,
            progress_stage=f"{progress_stage} H={horizon}",
        )
        report[str(horizon)] = {**counters, "metrics": _metrics(trades)}
    if progress is not None:
        progress.finish(stage=progress_stage)
    return report

def regime_trade_matrix(
    bars: Sequence[Bar],
    spreads: Sequence[int],
    model,
    *,
    symbol: str,
    point: float,
    base_settings: Settings,
    start: int,
    end: int,
    folds: int,
    horizons: Sequence[int],
    stride: int,
    fallback_spread_points: int,
    roundtrip_cost_r: float,
    progress: ProgressSlice | None = None,
    progress_stage: str = "regime trade matrix",
) -> dict[str, dict[str, object]]:
    """Trade metrics by market state, including independent chronological folds."""
    usable = end - start
    report: dict[str, dict[str, object]] = {}
    total_runs = max(1, len(horizons) * (folds + 1))
    local_progress = (
        ProgressSlice(progress.parent, progress.start, progress.end, total_runs, progress_stage)
        if progress is not None else None
    )
    run_no = 0
    for horizon in horizons:
        settings = replace(base_settings, horizon=horizon)
        if local_progress is not None:
            local_progress.update(run_no, stage=f"{progress_stage} H={horizon} overall")
        all_trades, _ = simulate(
            bars, spreads, model, symbol=symbol, point=point, settings=settings,
            start=start, end=end, stride=stride,
            fallback_spread_points=fallback_spread_points,
            roundtrip_cost_r=roundtrip_cost_r,
        )
        run_no += 1
        by_regime = _regime_metrics(all_trades)

        fold_rows = []
        for fold in range(folds):
            if progress is not None:
                progress.update(run_no, stage=f"{progress_stage} H={horizon} fold={fold+1}/{folds}")
            fold_start = start + usable * fold // folds
            fold_end = start + usable * (fold + 1) // folds
            fold_trades, _ = simulate(
                bars, spreads, model, symbol=symbol, point=point, settings=settings,
                start=fold_start, end=fold_end, stride=stride,
                fallback_spread_points=fallback_spread_points,
                roundtrip_cost_r=roundtrip_cost_r,
            )
            run_no += 1
            fold_rows.append({
                "fold": fold + 1,
                "window": _window_iso(bars, fold_start, fold_end),
                "by_regime": _regime_metrics(fold_trades),
            })

        states = sorted(set(by_regime) | {
            state
            for fold in fold_rows
            for state in fold["by_regime"]
        })
        stability = {}
        for state in states:
            fold_metrics = []
            profitable_folds = 0
            positive_mean_r_folds = 0
            for fold in fold_rows:
                metrics = fold["by_regime"].get(state)
                if metrics is None:
                    fold_metrics.append({
                        "fold": fold["fold"],
                        "trades": 0,
                        "profit_factor": None,
                        "mean_r": None,
                        "net_r": 0.0,
                        "max_drawdown_r": 0.0,
                    })
                    continue
                pf = metrics["profit_factor"]
                mean_r = metrics["mean_r"]
                if pf is not None and pf > 1.0:
                    profitable_folds += 1
                if mean_r is not None and mean_r > 0:
                    positive_mean_r_folds += 1
                fold_metrics.append({
                    "fold": fold["fold"],
                    "trades": metrics["trades"],
                    "profit_factor": pf,
                    "mean_r": mean_r,
                    "net_r": metrics["net_r"],
                    "max_drawdown_r": metrics["max_drawdown_r"],
                })
            stability[state] = {
                "overall": by_regime.get(state, _metrics([])),
                "profitable_folds": profitable_folds,
                "positive_mean_r_folds": positive_mean_r_folds,
                "folds_with_trades": sum(row["trades"] > 0 for row in fold_metrics),
                "folds": fold_metrics,
            }

        report[str(horizon)] = {
            "by_regime": by_regime,
            "stability": stability,
        }
    if local_progress is not None:
        local_progress.finish(stage=progress_stage)
    return report


def benchmark_model(
    bars: Sequence[Bar],
    spreads: Sequence[int],
    model,
    *,
    model_name: str,
    symbol: str,
    point: float,
    settings: Settings,
    folds: int,
    stride: int,
    fallback_spread_points: int,
    roundtrip_cost_r: float,
    analysis_horizons: Sequence[int],
    progress: ProgressSlice | None = None,
) -> dict[str, object]:
    if folds < 2:
        raise ValueError("folds must be >=2")
    warmup = max(256, len(bars) // 5)
    usable = len(bars) - warmup
    if usable < folds * (settings.horizon + 2):
        raise ValueError("not enough bars for requested folds")

    phase = lambda a, b, name: (
        ProgressSlice(progress.parent,
                      progress.start + (progress.end-progress.start)*a//100,
                      progress.start + (progress.end-progress.start)*b//100,
                      max(1, len(bars)-warmup),
                      f"{model_name}: {name}")
        if progress is not None else None
    )
    all_trades, counters = simulate(
        bars, spreads, model, symbol=symbol, point=point, settings=settings,
        start=warmup, end=len(bars), stride=stride,
        fallback_spread_points=fallback_spread_points,
        roundtrip_cost_r=roundtrip_cost_r,
        progress=phase(0, 12, "base replay"),
        progress_stage=f"{model_name}: base replay",
    )

    fold_reports = []
    for fold in range(folds):
        if progress is not None:
            progress.update(int(progress.total * (12 + 13 * fold / max(1, folds)) / 100),
                            stage=f"{model_name}: fold {fold+1}/{folds}")
        start = warmup + usable * fold // folds
        end = warmup + usable * (fold + 1) // folds
        trades, fold_counters = simulate(
            bars, spreads, model, symbol=symbol, point=point, settings=settings,
            start=start, end=end, stride=stride,
            fallback_spread_points=fallback_spread_points,
            roundtrip_cost_r=roundtrip_cost_r,
        )
        fold_reports.append({
            "fold": fold + 1,
            "window": _window_iso(bars, start, end),
            **fold_counters,
            "metrics": _metrics(trades),
        })

    return {
        "model": model_name,
        "stride": stride,
        "evaluation_window": _window_iso(bars, warmup, len(bars)),
        **counters,
        "metrics": _metrics(all_trades),
        "by_direction": _direction_metrics(all_trades),
        "by_year": _by_year(all_trades),
        "folds": fold_reports,
        "directional_accuracy": directional_accuracy(
            bars, model, start=warmup, end=len(bars),
            horizons=analysis_horizons, stride=stride,
            progress=phase(25, 43, "directional accuracy"),
            progress_stage=f"{model_name}: directional accuracy",
        ),
        "regime_directional_accuracy": regime_directional_accuracy(
            bars, spreads, model, symbol=symbol, point=point,
            start=warmup, end=len(bars), horizons=analysis_horizons, stride=stride,
            fallback_spread_points=fallback_spread_points,
            progress=phase(43, 61, "regime directional"),
            progress_stage=f"{model_name}: regime directional",
        ),
        "horizon_matrix": horizon_matrix(
            bars, spreads, model, symbol=symbol, point=point,
            base_settings=settings, start=warmup, end=len(bars),
            horizons=analysis_horizons, stride=stride,
            fallback_spread_points=fallback_spread_points,
            roundtrip_cost_r=roundtrip_cost_r,
            progress=phase(61, 78, "horizon matrix"),
            progress_stage=f"{model_name}: horizon matrix",
        ),
        "regime_trade_matrix": regime_trade_matrix(
            bars, spreads, model, symbol=symbol, point=point,
            base_settings=settings, start=warmup, end=len(bars), folds=folds,
            horizons=analysis_horizons, stride=stride,
            fallback_spread_points=fallback_spread_points,
            roundtrip_cost_r=roundtrip_cost_r,
            progress=phase(78, 100, "regime trade matrix"),
            progress_stage=f"{model_name}: regime trade matrix",
        ),
    }


def benchmark_database(
    db: str | Path,
    *,
    symbol: str = "XAUUSD_KAGGLE",
    point: float = 0.01,
    folds: int = 5,
    stride: int = 4,
    fallback_spread_points: int = 42,
    roundtrip_cost_r: float = 0.0,
    analysis_horizons: Sequence[int] = (1, 4, 8, 16),
    additional_models: Sequence[tuple[str, object, int]] = (),
    show_progress: bool = False,
) -> dict[str, object]:
    bars, spreads = load_bars(db, symbol)
    if len(bars) < 1200:
        raise ValueError("need >=1200 M15 bars for the historical benchmark")
    if not analysis_horizons or any(h < 1 for h in analysis_horizons):
        raise ValueError("analysis horizons must be positive")
    settings = replace(
        Settings(),
        require_direction_confirmation=False,
        market_state_policy_enabled=False,
    )
    previous = PreviousBarBaseline()
    momentum = MomentumBaseline()
    models = (
        ("previous_bar", previous, stride),
        ("momentum_4bar", momentum, stride),
        ("contrarian_previous_bar", ContrarianBaseline(previous), stride),
        ("contrarian_momentum_4bar", ContrarianBaseline(momentum), stride),
        *additional_models,
    )
    if any(model_stride < 1 for _, _, model_stride in models):
        raise ValueError("model strides must be positive")
    reporter = ProgressReporter(
        total=10_000,
        label="historical benchmark",
        enabled=show_progress,
        min_interval=1.0,
    )
    report = {
        "schema_version": 2,
        "dataset": {
            "db": str(db),
            "symbol": symbol,
            "timeframe": "M15",
            "bars": len(bars),
            "first_utc": datetime.fromtimestamp(bars[0].time, timezone.utc).isoformat(),
            "last_utc": datetime.fromtimestamp(bars[-1].time, timezone.utc).isoformat(),
        },
        "execution_assumptions": {
            "point": point,
            "stride": stride,
            "fallback_spread_points": fallback_spread_points,
            "roundtrip_cost_r": roundtrip_cost_r,
            "unknown_intrabar_order": "stop_first_if_stop_and_target_touch_same_M15_bar",
            "positions": "one_at_a_time_non_overlapping",
            "micro_bars": "unavailable_external_OHLC",
            "market_state_policy": False,
            "direction_confirmation": False,
        },
        "method": {
            "warmup": "first 20% excluded from scored evaluation; still available as prior context",
            "folds": folds,
            "fold_type": "chronological_non_overlapping_evaluation_windows",
            "note": "These reference models are not trained. Trainable models must fit only on data before each fold.",
            "analysis_horizons": list(analysis_horizons),
            "directional_accuracy": "raw forecast sign vs future close; no SL/TP or entry filters",
            "regime_directional_accuracy": "same raw direction metric grouped by price-only market state assessed from past/current bars only",
            "regime_trade_matrix": "trade-level PF/MeanR/NetR/DD by market state and horizon, with independent chronological fold replays",
            "contrarian": "mirror each baseline forecast around the latest completed close; no fitting",
        },
        "models": {},
        "limitations": [
            "External broker OHLC is not LiteFinance execution history.",
            "Spread is assumed where the external dataset has spread_points=0.",
            "M15 OHLC cannot reconstruct slippage, quote-level timing, or exact intrabar hit order.",
            "This lab does not reproduce RANGE execution or live EA exit management.",
            "Do not change live thresholds from this report alone.",
        ],
    }
    model_count = max(1, len(models))
    for pos, (name, model, model_stride) in enumerate(models):
        child = ProgressSlice(
            reporter,
            10_000 * pos // model_count,
            10_000 * (pos + 1) // model_count,
            10_000,
            name,
        )
        report["models"][name] = benchmark_model(
            bars, spreads, model, model_name=name, symbol=symbol, point=point,
            settings=settings, folds=folds, stride=model_stride,
            fallback_spread_points=fallback_spread_points,
            roundtrip_cost_r=roundtrip_cost_r,
            analysis_horizons=analysis_horizons,
            progress=child,
        )
    reporter.finish(stage="historical benchmark complete")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Ramon external M15 historical benchmark lab")
    parser.add_argument("--db", required=True)
    parser.add_argument("--symbol", default="XAUUSD_KAGGLE")
    parser.add_argument("--point", type=float, default=0.01)
    parser.add_argument("--folds", type=int, default=5)
    parser.add_argument("--stride", type=int, default=4)
    parser.add_argument("--fallback-spread", type=int, default=42)
    parser.add_argument("--cost-r", type=float, default=0.0)
    parser.add_argument("--analysis-horizons", default="1,4,8,16",
                        help="Comma-separated M15 horizons for raw direction and holding-period matrix")
    parser.add_argument("--include-chronos", action="store_true",
                        help="Also benchmark the project's Chronos-2 forecaster")
    parser.add_argument("--chronos-model", default="autogluon/chronos-2-small")
    parser.add_argument("--chronos-device", default="cpu")
    parser.add_argument("--chronos-stride", type=int, default=64,
                        help="Chronos screening stride; use --stride value for strict same-density comparison")
    parser.add_argument("--chronos-cache", default="data/chronos_historical_forecasts.sqlite3")
    parser.add_argument("--no-progress", action="store_true",
                        help="Disable stderr progress bar")
    parser.add_argument("--output", default="")
    args = parser.parse_args()
    horizons = tuple(int(value) for value in args.analysis_horizons.split(",") if value.strip())
    additional_models = []
    chronos_cache = None
    chronos_metadata = None
    if args.include_chronos:
        from .model import ChronosForecaster, model_name
        chronos = ChronosForecaster(model_name(args.chronos_model), args.chronos_device)
        identity = f"{args.chronos_model}@{chronos.revision or 'unknown-revision'}"
        chronos_cache = PersistentForecastCache(chronos, args.chronos_cache, identity)
        additional_models.append(("chronos", chronos_cache, args.chronos_stride))
        chronos_metadata = {
            "model": args.chronos_model,
            "revision": chronos.revision,
            "device": args.chronos_device,
            "stride": args.chronos_stride,
            "cache": args.chronos_cache,
        }
    report = benchmark_database(
        args.db,
        symbol=args.symbol,
        point=args.point,
        folds=args.folds,
        stride=args.stride,
        fallback_spread_points=args.fallback_spread,
        roundtrip_cost_r=args.cost_r,
        analysis_horizons=horizons,
        additional_models=additional_models,
        show_progress=not args.no_progress,
    )
    if chronos_cache is not None:
        chronos_metadata["cache_stats"] = chronos_cache.stats()
        chronos_cache.close()
        report["chronos"] = chronos_metadata
        if args.chronos_stride != args.stride:
            report["limitations"].append(
                "Chronos used a different screening stride from the baselines; rerun promising candidates "
                "with --chronos-stride equal to --stride before direct performance comparison."
            )
    payload = json.dumps(report, indent=2, allow_nan=False)
    if args.output:
        Path(args.output).write_text(payload + "\n", encoding="utf-8")
    print(payload)


if __name__ == "__main__":
    main()
