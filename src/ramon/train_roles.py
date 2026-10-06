"""Train roles on real closed trades with disjoint, purged temporal stacking.

Base roles never see meta-training or holdout outcomes. Frozen base predictions
feed the later meta window; no in-sample stacking or post-validation refit occurs.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from collections.abc import Callable

from .bundles import activate_bundle, atomic_json, load_active_bundle, stage_bundle
from .core import Bar, atr14
from .ensemble import (
    ENTRY_FEATURES, META_FEATURES, META_BASE_FEATURES, REGIME_FEATURES, RISK_FEATURES,
    BinaryLogisticModel, balanced_accuracy, regime_features, train_binary_logistic,
)
from .history import ensure_history_db, load_bars
from .news import NEWS_FEATURES

DEFAULT_MINIMUM_SAMPLES = 500
DEFAULT_REGIME_MINIMUM = 300
DEFAULT_MINIMUM_TRADES = 20


@dataclass(frozen=True)
class Example:
    time: int
    label_end: int
    features: dict[str, dict[str, float]]
    label: int
    net_r: float = 0.0
    base_trade: bool = False
    direction: str = "NONE"
    exit_reason: str = "UNKNOWN"


def _regime_dataset(bars: tuple[Bar, ...]) -> list[Example]:
    examples = []
    for index in range(32, len(bars) - 8):
        # No weekend/session gap may masquerade as an eight-bar horizon.
        if any(b.time - a.time != 900 for a, b in zip(bars[index:index + 8], bars[index + 1:index + 9])):
            continue
        context = bars[max(0, index - 255):index + 1]
        atr = atr14(context)
        if atr <= 0:
            continue
        path = [bar.close for bar in bars[index:index + 9]]
        net = abs(path[-1] - path[0])
        gross = sum(abs(b - a) for a, b in zip(path, path[1:]))
        label = int(net / atr >= 0.60 and net / max(gross, 1e-9) >= 0.50)
        examples.append(Example(
            bars[index].time + 900, bars[index + 8].time + 900,
            {"regime": regime_features(context)}, label,
        ))
    return examples


def load_trade_examples(db: str | Path, symbol: str, chronos_model: str) -> list[Example]:
    """Use one clean fully closed REAL position per decision, net of deal costs.

    Dashboard-opportunity entries are learnable when their executed direction
    matches the immutable snapshot direction. This allows human-confirmed model
    opportunities to improve Direction Quality / Entry / Meta / Risk without
    training on opposite-direction overrides. Manual exits, stop-outs and legacy expert exits without an exact trigger are
    censored. Legacy snapshots and 15-minute midpoint proxies are also excluded.
    Sample/trade ordering remains broker-event time; recorded UTC offsets are kept
    separately for audit and reporting rather than guessed for legacy events.
    """
    ensure_history_db(db)
    with sqlite3.connect(db) as conn:
        return read_trade_examples(conn, symbol, chronos_model)


def read_trade_examples(conn: sqlite3.Connection, symbol: str, chronos_model: str) -> list[Example]:
    """Shared trainer/audit selection; caller owns the transaction, no migrations."""
    rows = conn.execute("""
            SELECT s.quote_time,t.closed,s.regime_features,s.entry_features,
                   s.news_features,s.meta_base_features,t.net_r,s.base_decision,t.direction,t.exit_reason
            FROM decision_samples s JOIN trade_outcomes t ON t.sample_key=s.sample_key
            WHERE s.symbol=? AND t.symbol=s.symbol AND s.chronos_model=?
              AND s.schema_version>=3 AND s.news_features IS NOT NULL
              AND t.direction=s.direction AND t.training_status='LEARNABLE'
              AND (
                    s.final_decision=t.direction
                    OR COALESCE(t.entry_source,'AUTO_RAMON')='DASHBOARD_OPPORTUNITY'
                  )
              AND t.opened>=s.quote_time
              AND t.opened<=s.quote_time+90 AND t.closed>=t.opened
            ORDER BY s.quote_time,s.sample_key
        """, (symbol, chronos_model)).fetchall()
    return [Example(int(t), int(end), {"regime": json.loads(r), "entry": json.loads(e),
                                     "news": json.loads(n), "meta_base": json.loads(m)}, int(float(pnl) > 0),
                    float(pnl), base in {"BUY", "SELL"}, direction, exit_reason)
            for t, end, r, e, n, m, pnl, base, direction, exit_reason in rows]


def temporal_windows(examples: list[Example]) -> tuple[list[Example], list[Example], list[Example]]:
    """50% base / 30% meta / 20% final holdout; purge every crossing label."""
    if len(examples) < 100:
        raise ValueError("need at least 100 closed trades for three temporal windows")
    ordered = sorted(examples, key=lambda row: row.time)
    meta_start = ordered[len(ordered) // 2].time
    holdout_start = ordered[len(ordered) * 8 // 10].time
    base = [row for row in ordered if row.time < meta_start and row.label_end < meta_start]
    meta = [row for row in ordered if meta_start <= row.time < holdout_start and row.label_end < holdout_start]
    holdout = [row for row in ordered if row.time >= holdout_start]
    return base, meta, holdout


def fit_role(examples: list[Example], role: str, names: tuple[str, ...]) -> BinaryLogisticModel:
    labels = [row.label for row in examples]
    if len(examples) < 40 or min(labels.count(0), labels.count(1)) < 10:
        raise ValueError(f"{role}: need >=40 samples and >=10 of each class in training window")
    return train_binary_logistic(
        [row.features[role] for row in examples], labels, names,
        metadata={"role": role, "samples": len(examples),
                  "last_feature_time": max(row.time for row in examples),
                  "last_label_end": max(row.label_end for row in examples)},
    )


def risk_features(row: Example) -> dict[str, float]:
    """Reconstruct live risk-role features from immutable entry snapshots."""
    entry = row.features["entry"]
    regime = row.features["regime"]
    if row.direction not in {"BUY", "SELL"}:
        raise ValueError("risk: missing executed direction")
    sign = -1.0 if row.direction == "SELL" else 1.0
    return {
        "side_sell": 1.0 if row.direction == "SELL" else 0.0,
        "edge_ratio": entry["edge_ratio"],
        "signal_strength": entry["signal_strength"],
        "uncertainty_atr": entry["uncertainty_atr"],
        "intrabar_move_atr": entry["intrabar_move_atr"],
        "intrabar_rebound_atr": entry["intrabar_rebound_atr"],
        "ai_trend_score": entry["ai_trend_score"],
        "ai_trend_consistency": entry["ai_trend_consistency"],
        "spread_atr": entry["spread_atr"],
        "forecast_distance_atr": entry["forecast_distance_atr"],
        "ret_1_atr_aligned": sign * regime["ret_1_atr"],
        "ret_4_atr_aligned": sign * regime["ret_4_atr"],
        "ret_12_atr_aligned": sign * regime["ret_12_atr"],
        "range_12_atr": regime["range_12_atr"],
        "body_efficiency_12": regime["body_efficiency_12"],
        "atr_pct": regime["atr_pct"],
    }


def fit_risk_role(examples: list[Example]) -> BinaryLogisticModel:
    """Predict the probability that an otherwise valid entry ends at the full SL."""
    labels = [int(row.exit_reason == "DEAL_REASON_SL") for row in examples]
    if len(examples) < 40 or min(labels.count(0), labels.count(1)) < 10:
        raise ValueError("risk: need >=40 samples and >=10 of each class in training window")
    return train_binary_logistic(
        [risk_features(row) for row in examples],
        labels,
        RISK_FEATURES,
        metadata={"role": "risk", "target": "full_stop_loss", "samples": len(examples),
                  "last_feature_time": max(row.time for row in examples),
                  "last_label_end": max(row.label_end for row in examples)},
    )


def fit_direction_quality_role(
    examples: list[Example], direction: str
) -> BinaryLogisticModel:
    """Predict win probability for one executed side, display-only in shadow mode."""
    if direction not in {"BUY", "SELL"}:
        raise ValueError("direction quality: direction must be BUY or SELL")
    rows = [row for row in examples if row.direction == direction]
    labels = [row.label for row in rows]
    role = f"{direction.lower()}_quality"
    if len(rows) < 40 or min(labels.count(0), labels.count(1)) < 10:
        raise ValueError(
            f"{role}: need >=40 samples and >=10 wins/losses for this direction"
        )
    return train_binary_logistic(
        [risk_features(row) for row in rows],
        labels,
        RISK_FEATURES,
        metadata={
            "role": role,
            "target": "win",
            "direction": direction,
            "samples": len(rows),
            "last_feature_time": max(row.time for row in rows),
            "last_label_end": max(row.label_end for row in rows),
        },
    )


def meta_features(row: Example, models: dict[str, BinaryLogisticModel]) -> dict[str, float]:
    features = dict(row.features["meta_base"])
    features["regime_probability"] = models["regime"].predict_proba(row.features["regime"])
    features["entry_probability"] = models["entry"].predict_proba(row.features["entry"])
    if "news" in models:
        features["news_probability"] = models["news"].predict_proba(row.features["news"])
    return features


def trade_metrics(examples: list[Example], accepted: list[bool]) -> dict[str, float | int]:
    equity = peak = drawdown = 0.0
    count = 0
    # Closed-trade replay on observed executed opportunities; never invent WAIT payoffs.
    # Apply single-position discipline if multiple terminals produced overlapping rows.
    free_at = -1
    for row, accept in zip(examples, accepted):
        if not accept or row.time <= free_at:
            continue
        free_at = row.label_end
        count += 1
        equity += row.net_r
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    return {"trades": count, "net_r": equity, "max_drawdown_r": drawdown}


def evaluate_roles(models: dict[str, BinaryLogisticModel], examples: list[Example], threshold: float) -> dict:
    features = [meta_features(row, models) for row in examples]
    probabilities = [models["meta"].predict_proba(row) for row in features]
    labels = [row.label for row in examples]
    accepted = [p >= threshold and row.features["entry"]["edge_ratio"] > 0
                for row, p in zip(examples, probabilities)]
    result = {**trade_metrics(examples, accepted),
              "balanced_accuracy": balanced_accuracy(models["meta"], features, labels),
              "brier": sum((p - y) ** 2 for p, y in zip(probabilities, labels)) / len(labels)}
    if "risk" in models:
        risk_rows = [risk_features(row) for row in examples]
        risk_probabilities = [models["risk"].predict_proba(row) for row in risk_rows]
        risk_labels = [int(row.exit_reason == "DEAL_REASON_SL") for row in examples]
        result["risk_balanced_accuracy"] = balanced_accuracy(models["risk"], risk_rows, risk_labels)
        result["risk_brier"] = sum((p - y) ** 2 for p, y in zip(risk_probabilities, risk_labels)) / len(risk_labels)
    return result


def promotion_gate(candidate: dict, incumbent: dict, baseline: dict,
                   *, minimum_trades: int, improvement: float, maximum_drawdown: float) -> list[str]:
    reasons = []
    if candidate["trades"] < minimum_trades:
        reasons.append("insufficient_holdout_trades")
    if candidate["balanced_accuracy"] < 0.52:
        reasons.append("weak_holdout_balanced_accuracy")
    if candidate.get("risk_balanced_accuracy", 1.0) < 0.52:
        reasons.append("weak_holdout_risk_balanced_accuracy")
    if candidate["net_r"] <= 0:
        reasons.append("nonpositive_holdout_net_r")
    for name, reference in (("incumbent", incumbent), ("chronos_baseline", baseline)):
        if candidate["net_r"] < reference["net_r"] + improvement:
            reasons.append(f"no_net_r_improvement_over_{name}")
        if candidate["max_drawdown_r"] > reference["max_drawdown_r"] + 1.0:
            reasons.append(f"drawdown_worse_than_{name}")
    if candidate["max_drawdown_r"] > maximum_drawdown:
        reasons.append("holdout_drawdown_limit")
    if "brier" in incumbent and candidate["brier"] > incumbent["brier"]:
        reasons.append("probability_quality_worse_than_incumbent")
    return reasons


def train_bundle(*, db: str | Path, symbol: str, chronos_model: str, out: Path,
                 minimum_samples: int = DEFAULT_MINIMUM_SAMPLES, regime_minimum: int = DEFAULT_REGIME_MINIMUM,
                 minimum_trades: int = DEFAULT_MINIMUM_TRADES, threshold: float = 0.65,
                 improvement: float = 0.5, maximum_drawdown: float = 8.0,
                 progress: Callable[[str, str, dict[str, object]], None] | None = None) -> dict:
    def emit(stage: str, message: str, **details: object) -> None:
        if progress is not None:
            progress(stage, message, details)
    if minimum_samples < 100 or regime_minimum < 40 or minimum_trades < 1:
        raise ValueError("invalid sample limits")
    if not 0.5 <= threshold < 1 or improvement <= 0 or maximum_drawdown <= 0:
        raise ValueError("invalid promotion limits")
    emit("load_data", "در حال بارگذاری نمونه‌های واقعی بسته‌شده از دیتابیس")
    examples = load_trade_examples(db, symbol, chronos_model)
    emit("load_data", "نمونه‌های قابل آموزش بارگذاری شدند", samples=len(examples))
    report = {"updated": False, "closed_trade_samples": len(examples), "chronos_model": chronos_model,
              "symbol": symbol, "generated_at_utc": datetime.now(timezone.utc).isoformat(),
              "minimum_samples": minimum_samples, "regime_minimum": regime_minimum,
              "minimum_trades": minimum_trades,
              "remaining_to_sample_gate": max(0, minimum_samples - len(examples)),
              "dataset_source": "clean_real_closed_positions_net_of_deal_costs",
              "label_policy": "exclude_manual_stopout_unknown_expert"}
    if len(examples) < minimum_samples:
        emit("sample_gate", "حداقل نمونهٔ واقعی هنوز تأمین نشده", samples=len(examples), minimum_samples=minimum_samples)
        return {**report, "status": "waiting_for_closed_trades", "minimum_samples": minimum_samples}
    emit("windows", "در حال ساخت پنجره‌های زمانی Train / Meta / Holdout")
    base, meta, holdout = temporal_windows(examples)
    emit("windows", "پنجره‌های زمانی ساخته شدند", base=len(base), meta=len(meta), holdout=len(holdout))
    if not base or not meta or len(holdout) < 20:
        emit("windows", "پنجره‌های زمانی کافی نیستند", base=len(base), meta=len(meta), holdout=len(holdout))
        return {**report, "status": "insufficient_purged_windows"}
    # Freeze this holdout for one attempt. Later runs must use entirely new outcomes,
    # avoiding repeated tuning and promotion on an already inspected holdout.
    state_path = out / "evaluation_state.json"
    if state_path.exists():
        state = json.loads(state_path.read_text())
        holdout = [row for row in holdout if row.time > int(state["last_evaluated_end"])]
    if len(holdout) < 20:
        emit("holdout", "Holdout تازه برای ارزیابی کافی نیست", holdout=len(holdout))
        return {**report, "status": "waiting_for_fresh_holdout"}
    incumbent_manifest = None
    incumbent_models = None
    if (out / "active.json").exists():
        incumbent_manifest, incumbent_models = load_active_bundle(out, chronos_model)
        if any(row.time <= int(incumbent_manifest["training_label_end"]) for row in holdout):
            return {**report, "status": "incumbent_holdout_overlap"}
    emit("regime_data", "در حال آماده‌سازی دیتاست Regime از تاریخچهٔ M15")
    bars, _ = load_bars(db, symbol)
    regime = [row for row in _regime_dataset(bars) if row.label_end < meta[0].time]
    emit("regime_data", "دیتاست Regime آماده شد", samples=len(regime), bars=len(bars))
    if len(regime) < regime_minimum:
        emit("regime_data", "دادهٔ Regime هنوز کافی نیست", samples=len(regime), minimum=regime_minimum)
        return {**report, "status": "waiting_for_regime_history", "regime_samples": len(regime)}
    try:
        emit("train_regime", "آموزش مدل Regime آغاز شد", samples=len(regime))
        regime_model = fit_role(regime, "regime", REGIME_FEATURES)
        emit("train_regime", "آموزش مدل Regime تمام شد")
        emit("train_entry", "آموزش مدل Entry آغاز شد", samples=len(base))
        entry_model = fit_role(base, "entry", ENTRY_FEATURES)
        emit("train_entry", "آموزش مدل Entry تمام شد")
        emit("train_news", "آموزش مدل News آغاز شد", samples=len(base))
        news_model = fit_role(base, "news", NEWS_FEATURES)
        emit("train_news", "آموزش مدل News تمام شد")
        emit("train_risk", "آموزش مدل Risk / SL آغاز شد", samples=len(base))
        risk_model = fit_risk_role(base)
        emit("train_risk", "آموزش مدل Risk / SL تمام شد")
        models = {"regime": regime_model, "entry": entry_model, "news": news_model, "risk": risk_model}
        meta_examples = [Example(row.time, row.label_end, {"meta": meta_features(row, models)}, row.label)
                         for row in meta]
        emit("train_meta", "آموزش مدل Meta آغاز شد", samples=len(meta_examples))
        models["meta"] = fit_role(meta_examples, "meta", META_FEATURES)
        emit("train_meta", "آموزش مدل Meta تمام شد")
    except ValueError as exc:
        emit("training_error", "آموزش یکی از Roleها به‌دلیل کمبود کلاس متوقف شد", reason=str(exc))
        return {**report, "status": "waiting_for_training_classes", "reason": str(exc)}
    emit("holdout", "ارزیابی مدل کاندید روی Holdout واقعی آغاز شد", samples=len(holdout))
    candidate = evaluate_roles(models, holdout, threshold)
    emit("holdout", "ارزیابی Holdout تمام شد", trades=candidate.get("trades"), net_r=candidate.get("net_r"), balanced_accuracy=candidate.get("balanced_accuracy"))
    # Compare the ensemble against a single learned gate using Chronos features only.
    # This is a reported ablation, never a tuned threshold or a replacement live model.
    simple = fit_role([Example(row.time, row.label_end, {"meta": row.features["meta_base"]}, row.label)
                       for row in meta], "meta", META_BASE_FEATURES)
    simple_models = {**models, "meta": simple}
    ablation = evaluate_roles(simple_models, holdout, threshold)
    baseline = trade_metrics(holdout, [row.base_trade for row in holdout])
    incumbent = (evaluate_roles(incumbent_models, holdout, float(incumbent_manifest["trade_threshold"]))
                 if incumbent_models else baseline)
    emit("promotion_gate", "در حال بررسی Promotion Gate")
    reasons = promotion_gate(candidate, incumbent, baseline, minimum_trades=minimum_trades,
                             improvement=improvement, maximum_drawdown=maximum_drawdown)
    emit("promotion_gate", "Promotion Gate بررسی شد", passed=not reasons, reasons=reasons)
    # Prevent another daily run from selecting against the same outcomes, even on rejection.
    atomic_json(state_path, {"last_evaluated_end": max(row.label_end for row in holdout)})
    metadata = {"chronos_model": chronos_model, "symbol": symbol, "trade_threshold": threshold,
                "dataset_source": report["dataset_source"], "promotion_gate_passed": not reasons,
                "training_label_end": max(row.label_end for row in meta),
                "base_label_end": max(row.label_end for row in base),
                "regime_label_end": max(row.label_end for row in regime),
                "meta_start": meta[0].time, "holdout_start": holdout[0].time,
                "holdout_end": max(row.label_end for row in holdout),
                "window_samples": {"base": len(base), "meta": len(meta), "holdout": len(holdout)},
                "candidate": candidate, "incumbent": incumbent, "chronos_baseline": baseline,
                "chronos_features_only_ablation": ablation,
                "gate_reasons": reasons,
                "evaluation_scope": "filtering_recorded_executed_opportunities_not_full_strategy_backtest"}
    emit("stage_bundle", "در حال ذخیرهٔ Bundle کاندید")
    bundle_id = stage_bundle(out, models, metadata)
    emit("stage_bundle", "Bundle کاندید ذخیره شد", bundle_id=bundle_id)
    if not reasons:
        emit("activate", "مدل کاندید تأیید شد؛ در حال فعال‌سازی Bundle")
        activate_bundle(out, bundle_id, chronos_model)
        emit("activate", "Bundle تأییدشده فعال شد", bundle_id=bundle_id)
    else:
        emit("rejected", "کاندید رد شد؛ مدل فعلی بدون تغییر ماند", reasons=reasons)
    return {**report, **metadata, "bundle_id": bundle_id, "updated": not reasons,
            "status": "promoted" if not reasons else "validation_rejected"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="/data/ramon_history.sqlite3")
    parser.add_argument("--symbol", default="XAUUSD_l")
    parser.add_argument("--out", default="/checkpoints/ensemble")
    parser.add_argument("--chronos-model", default="autogluon/chronos-2-small")
    parser.add_argument("--active-model-file", default="/checkpoints/active_model.txt")
    parser.add_argument("--minimum-samples", type=int, default=DEFAULT_MINIMUM_SAMPLES)
    parser.add_argument("--regime-min-samples", type=int, default=DEFAULT_REGIME_MINIMUM)
    parser.add_argument("--minimum-trades", type=int, default=DEFAULT_MINIMUM_TRADES)
    args = parser.parse_args()
    model = args.chronos_model
    active_model = Path(args.active_model_file)
    if active_model.is_file():
        model = active_model.read_text().strip() or model
    out = Path(args.out)
    report = train_bundle(db=args.db, symbol=args.symbol, chronos_model=model, out=out,
                          minimum_samples=args.minimum_samples, regime_minimum=args.regime_min_samples,
                          minimum_trades=args.minimum_trades)
    atomic_json(out / "training_report.json", report)
    print(json.dumps(report))


if __name__ == "__main__":
    main()
