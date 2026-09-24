from __future__ import annotations

from dataclasses import dataclass
import json
from math import exp, isfinite
from pathlib import Path
from typing import Mapping, Sequence

from .core import Bar, Decision, Market, atr14
from .news import NEWS_FEATURES, neutral_news_features


REGIME_FEATURES = (
    "ret_1_atr",
    "ret_4_atr",
    "ret_12_atr",
    "range_4_atr",
    "range_12_atr",
    "body_efficiency_12",
    "atr_pct",
)

ENTRY_FEATURES = (
    "edge_ratio",
    "signal_strength",
    "uncertainty_atr",
    "intrabar_move_atr",
    "intrabar_rebound_atr",
    "ai_trend_score",
    "ai_trend_consistency",
    "spread_atr",
    "forecast_distance_atr",
)

META_BASE_FEATURES = (
    "edge_ratio",
    "signal_strength",
    "uncertainty_atr",
    "ai_trend_score",
    "ai_trend_consistency",
)

LEGACY_META_FEATURES = META_BASE_FEATURES + ("regime_probability", "entry_probability")
META_FEATURES = LEGACY_META_FEATURES + ("news_probability",)

RISK_FEATURES = (
    "side_sell",
    "edge_ratio",
    "signal_strength",
    "uncertainty_atr",
    "intrabar_move_atr",
    "intrabar_rebound_atr",
    "ai_trend_score",
    "ai_trend_consistency",
    "spread_atr",
    "forecast_distance_atr",
    "ret_1_atr_aligned",
    "ret_4_atr_aligned",
    "ret_12_atr_aligned",
    "range_12_atr",
    "body_efficiency_12",
    "atr_pct",
)

RISK_MULTIPLIER_MIN = 0.50
RISK_MULTIPLIER_MAX = 1.50


@dataclass(frozen=True, slots=True)
class BinaryLogisticModel:
    feature_names: tuple[str, ...]
    means: tuple[float, ...]
    scales: tuple[float, ...]
    weights: tuple[float, ...]
    bias: float
    metadata: dict[str, object]

    @classmethod
    def load(cls, path: str | Path) -> BinaryLogisticModel:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        model = cls(
            feature_names=tuple(str(v) for v in raw["feature_names"]),
            means=tuple(float(v) for v in raw["means"]),
            scales=tuple(float(v) for v in raw["scales"]),
            weights=tuple(float(v) for v in raw["weights"]),
            bias=float(raw["bias"]),
            metadata=dict(raw.get("metadata", {})),
        )
        count = len(model.feature_names)
        if not count or len(set(model.feature_names)) != count:
            raise ValueError("invalid feature names")
        if any(len(v) != count for v in (model.means, model.scales, model.weights)):
            raise ValueError("model vector length mismatch")
        if not all(isfinite(v) for v in (*model.means, *model.scales, *model.weights, model.bias)):
            raise ValueError("non-finite model parameters")
        if any(v <= 0 for v in model.scales):
            raise ValueError("invalid model scales")
        return model

    def save(self, path: str | Path) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(
                {
                    "feature_names": list(self.feature_names),
                    "means": list(self.means),
                    "scales": list(self.scales),
                    "weights": list(self.weights),
                    "bias": self.bias,
                    "metadata": self.metadata,
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    def predict_proba(self, features: Mapping[str, float]) -> float:
        values = []
        for name, mean, scale in zip(self.feature_names, self.means, self.scales):
            value = float(features[name])
            if not isfinite(value):
                raise ValueError(f"non-finite model feature {name}")
            values.append((value - mean) / scale)
        z = self.bias + sum(w * x for w, x in zip(self.weights, values))
        if not isfinite(z):
            raise ValueError("non-finite model score")
        if z >= 0:
            return 1.0 / (1.0 + exp(-min(z, 60.0)))
        ez = exp(max(z, -60.0))
        return ez / (1.0 + ez)


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values)


def train_binary_logistic(
    rows: Sequence[Mapping[str, float]],
    labels: Sequence[int],
    feature_names: Sequence[str],
    *,
    steps: int = 800,
    learning_rate: float = 0.04,
    l2: float = 0.001,
    metadata: dict[str, object] | None = None,
) -> BinaryLogisticModel:
    if len(rows) != len(labels) or len(rows) < 20:
        raise ValueError("need at least 20 aligned training samples")
    if set(labels) != {0, 1}:
        raise ValueError("binary training requires both classes")
    names = tuple(feature_names)
    columns = [[float(row[name]) for row in rows] for name in names]
    if not names or any(not isfinite(value) for column in columns for value in column):
        raise ValueError("invalid training features")
    means = tuple(_mean(column) for column in columns)
    scales = tuple(
        max((_mean([(value - mean) ** 2 for value in column])) ** 0.5, 1e-9)
        for column, mean in zip(columns, means)
    )
    matrix = [
        tuple((float(row[name]) - mean) / scale for name, mean, scale in zip(names, means, scales))
        for row in rows
    ]
    weights = [0.0] * len(names)
    bias = 0.0
    n = float(len(rows))

    for _ in range(steps):
        grad_w = [0.0] * len(weights)
        grad_b = 0.0
        for values, label in zip(matrix, labels):
            z = bias + sum(w * x for w, x in zip(weights, values))
            if z >= 0:
                probability = 1.0 / (1.0 + exp(-min(z, 60.0)))
            else:
                ez = exp(max(z, -60.0))
                probability = ez / (1.0 + ez)
            error = probability - float(label)
            grad_b += error
            for index, value in enumerate(values):
                grad_w[index] += error * value
        bias -= learning_rate * grad_b / n
        for index in range(len(weights)):
            gradient = grad_w[index] / n + l2 * weights[index]
            weights[index] -= learning_rate * gradient

    return BinaryLogisticModel(
        feature_names=names,
        means=means,
        scales=scales,
        weights=tuple(weights),
        bias=bias,
        metadata=dict(metadata or {}),
    )


def balanced_accuracy(model: BinaryLogisticModel, rows: Sequence[Mapping[str, float]], labels: Sequence[int]) -> float:
    positives = negatives = positive_ok = negative_ok = 0
    for row, label in zip(rows, labels):
        prediction = 1 if model.predict_proba(row) >= 0.5 else 0
        if label:
            positives += 1
            positive_ok += int(prediction == 1)
        else:
            negatives += 1
            negative_ok += int(prediction == 0)
    if positives == 0 or negatives == 0:
        return 0.0
    return 0.5 * (positive_ok / positives + negative_ok / negatives)


def regime_features(bars: Sequence[Bar]) -> dict[str, float]:
    if len(bars) < 16:
        raise ValueError("need >=16 bars for regime features")
    atr = atr14(bars)
    if atr <= 0:
        raise ValueError("invalid ATR")
    close = bars[-1].close
    recent4 = bars[-4:]
    recent12 = bars[-12:]
    path = [bar.close for bar in recent12]
    gross = sum(abs(b - a) for a, b in zip(path, path[1:]))
    net = abs(path[-1] - path[0])
    return {
        "ret_1_atr": (close - bars[-2].close) / atr,
        "ret_4_atr": (close - bars[-5].close) / atr,
        "ret_12_atr": (close - bars[-13].close) / atr,
        "range_4_atr": (max(bar.high for bar in recent4) - min(bar.low for bar in recent4)) / atr,
        "range_12_atr": (max(bar.high for bar in recent12) - min(bar.low for bar in recent12)) / atr,
        "body_efficiency_12": net / max(gross, 1e-9),
        "atr_pct": atr / close,
    }


def entry_features(market: Market, decision: Decision) -> dict[str, float]:
    dominant_edge = max(decision.buy_edge, decision.sell_edge)
    midpoint = (market.bid + market.ask) / 2.0
    spread = market.ask - market.bid
    atr = max(decision.atr, market.point)
    return {
        "edge_ratio": dominant_edge / max(decision.minimum_edge, market.point),
        "signal_strength": decision.signal_strength,
        "uncertainty_atr": decision.uncertainty / atr,
        "intrabar_move_atr": decision.intrabar_move_atr,
        "intrabar_rebound_atr": decision.intrabar_rebound_atr,
        "ai_trend_score": decision.ai_trend_score,
        "ai_trend_consistency": decision.ai_trend_consistency,
        "spread_atr": spread / atr,
        "forecast_distance_atr": abs(decision.forecast_median - midpoint) / atr,
    }


def meta_base_features(market: Market, decision: Decision) -> dict[str, float]:
    dominant_edge = max(decision.buy_edge, decision.sell_edge)
    atr = max(decision.atr, market.point)
    return {
        "edge_ratio": dominant_edge / max(decision.minimum_edge, market.point),
        "signal_strength": decision.signal_strength,
        "uncertainty_atr": decision.uncertainty / atr,
        "ai_trend_score": decision.ai_trend_score,
        "ai_trend_consistency": decision.ai_trend_consistency,
    }


def dominant_direction(decision: Decision) -> str:
    if max(decision.buy_edge, decision.sell_edge) <= 0:
        return "NONE"
    return "BUY" if decision.buy_edge >= decision.sell_edge else "SELL"


def risk_features(market: Market, decision: Decision) -> dict[str, float]:
    """Entry-time features for the learned risk-sizing role."""
    entry = entry_features(market, decision)
    regime = regime_features(market.bars)
    direction = dominant_direction(decision)
    sign = -1.0 if direction == "SELL" else 1.0
    return {
        "side_sell": 1.0 if direction == "SELL" else 0.0,
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


def probability_to_risk_multiplier(probability: float) -> float:
    """Map learned win probability to a bounded live sizing multiplier."""
    if not isfinite(probability) or not 0.0 <= probability <= 1.0:
        raise ValueError("invalid risk probability")
    return min(RISK_MULTIPLIER_MAX, max(RISK_MULTIPLIER_MIN, 2.0 * probability))


class EnsembleCoordinator:
    """Learned regime, entry, news and meta roles around Chronos."""

    def __init__(self, root: str | Path, chronos_model: str | None = None) -> None:
        self.root = Path(root)
        self.regime = self.entry = self.news = self.meta = self.risk = None
        self.manifest: dict[str, object] = {}
        self.bundle_id = ""
        self.symbol = ""
        self.error = ""
        self.threshold = 0.65
        if (self.root / "active.json").exists():
            from .bundles import load_active_bundle

            try:
                manifest, models = load_active_bundle(self.root, chronos_model)
                self.regime = models.get("regime")
                self.entry = models.get("entry")
                self.news = models.get("news")
                self.meta = models.get("meta")
                self.risk = models.get("risk")
                self.bundle_id = str(manifest["bundle_id"])
                self.manifest = manifest
                self.symbol = str(manifest["symbol"])
                self.threshold = float(manifest["trade_threshold"])
            except (OSError, ValueError, KeyError, TypeError) as exc:
                self.regime = self.entry = self.news = self.meta = self.risk = None
                self.error = f"invalid_role_bundle: {exc}"

    @property
    def ready(self) -> bool:
        return self.regime is not None and self.entry is not None and self.meta is not None

    @property
    def news_ready(self) -> bool:
        return self.news is not None

    @property
    def risk_ready(self) -> bool:
        return self.risk is not None

    def status(self) -> dict[str, object]:
        return {
            "ensemble_ready": self.ready,
            "regime_model_ready": self.regime is not None,
            "entry_model_ready": self.entry is not None,
            "news_model_ready": self.news is not None,
            "meta_model_ready": self.meta is not None,
            "risk_model_ready": self.risk_ready,
            "ensemble_bundle": self.bundle_id,
            "ensemble_error": self.error,
            "ensemble_mode": ("meta_news" if self.ready and self.news_ready else "meta_legacy") if self.ready else ("blocked" if self.error else "bootstrap_chronos"),
        }

    def assess(
        self,
        market: Market,
        decision: Decision,
        news_features: Mapping[str, float] | None = None,
    ) -> tuple[dict[str, object], dict[str, dict[str, float]]]:
        r_features = regime_features(market.bars)
        e_features = entry_features(market, decision)
        n_features = dict(news_features or neutral_news_features())
        m_base = meta_base_features(market, decision)
        regime_probability = self.regime.predict_proba(r_features) if self.regime else -1.0
        entry_probability = self.entry.predict_proba(e_features) if self.entry else -1.0
        news_probability = self.news.predict_proba(n_features) if self.news else -1.0
        risk_probability = self.risk.predict_proba(risk_features(market, decision)) if self.risk else -1.0
        risk_multiplier = probability_to_risk_multiplier(risk_probability) if self.risk else 1.0

        meta_probability = -1.0
        final_decision = decision.decision
        final_reason = decision.reason
        ensemble_active = 0

        if self.error:
            final_decision, final_reason = "WAIT", "invalid_role_bundle"

        if self.ready:
            meta_features = dict(m_base)
            meta_features["regime_probability"] = regime_probability
            meta_features["entry_probability"] = entry_probability
            if self.news is not None:
                meta_features["news_probability"] = news_probability
            meta_probability = self.meta.predict_proba(meta_features)
            ensemble_active = 1
            direction = dominant_direction(decision)
            dominant_edge = max(decision.buy_edge, decision.sell_edge)

            if meta_probability >= self.threshold and dominant_edge > 0 and market.symbol == self.symbol:
                final_decision = direction
                final_reason = (
                    "ensemble_meta_up" if direction == "BUY" else "ensemble_meta_down"
                )
            else:
                final_decision = "WAIT"
                final_reason = "ensemble_meta_veto"

        payload: dict[str, object] = {
            "base_decision": decision.decision,
            "base_reason": decision.reason,
            "decision": final_decision,
            "reason": final_reason,
            "ensemble_ready": int(self.ready),
            "ensemble_active": ensemble_active,
            "regime_probability": regime_probability,
            "entry_probability": entry_probability,
            "news_probability": news_probability,
            "meta_probability": meta_probability,
            "risk_model_ready": int(self.risk_ready),
            "risk_probability": risk_probability,
            "risk_multiplier": risk_multiplier,
            "edge": max(decision.buy_edge, decision.sell_edge) if final_decision in {"BUY", "SELL"} else 0.0,
            "ensemble_bundle": self.bundle_id,
        }
        feature_snapshot = {
            "regime": r_features,
            "entry": e_features,
            "news": n_features,
            "meta_base": m_base,
        }
        return payload, feature_snapshot
