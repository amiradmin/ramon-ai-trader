from __future__ import annotations

from pathlib import Path

from ramon.core import Bar, Decision, Market
from ramon.ensemble import (
    BinaryLogisticModel,
    EnsembleCoordinator,
    balanced_accuracy,
    probability_to_risk_multiplier,
    train_binary_logistic,
)


def test_binary_role_model_learns_separable_samples(tmp_path: Path) -> None:
    rows = [{"x": -2.0}, {"x": -1.0}, {"x": -0.5}, {"x": 0.5}, {"x": 1.0}, {"x": 2.0}] * 8
    labels = [0, 0, 0, 1, 1, 1] * 8
    model = train_binary_logistic(rows, labels, ("x",), steps=1200)

    assert balanced_accuracy(model, rows, labels) > 0.95

    path = tmp_path / "model.json"
    model.save(path)
    loaded = BinaryLogisticModel.load(path)
    assert loaded.predict_proba({"x": 2.0}) > 0.8
    assert loaded.predict_proba({"x": -2.0}) < 0.2


def _market() -> Market:
    bars = tuple(
        Bar(
            1_800_000_000 + index * 900,
            100.0 + index * 0.01,
            100.5 + index * 0.01,
            99.5 + index * 0.01,
            100.0 + index * 0.01,
        )
        for index in range(128)
    )
    micro = (
        Bar(1_800_200_000, 101.0, 101.1, 100.9, 101.0),
        Bar(1_800_200_060, 101.0, 101.2, 100.95, 101.1),
        Bar(1_800_200_120, 101.1, 101.3, 101.05, 101.2),
        Bar(1_800_200_180, 101.2, 101.4, 101.15, 101.3),
    )
    return Market("XAUUSD_l", "M15", 101.30, 101.42, 0.01, bars, micro)


def _decision() -> Decision:
    return Decision(
        decision="WAIT",
        reason="insufficient_model_strength",
        signal_bar_time=1_800_000_000 + 127 * 900,
        signal_bid=101.30,
        signal_ask=101.42,
        spread_points=12,
        atr=1.0,
        edge=0.0,
        buy_edge=0.7,
        sell_edge=-0.9,
        minimum_edge=0.6,
        uncertainty=4.0,
        signal_strength=0.175,
        minimum_strength=0.2,
        intrabar_confirmed=0,
        intrabar_direction="BUY",
        intrabar_move_atr=0.1,
        intrabar_rebound_atr=0.15,
        intrabar_min_strength=0.05,
        intrabar_min_move_atr=0.06,
        intrabar_min_rebound_atr=0.08,
        ai_trend_confirmed=0,
        ai_trend_direction="BUY",
        ai_trend_score=0.3,
        ai_trend_move_atr=0.3,
        ai_trend_consistency=1.0,
        trend_min_path_atr=0.15,
        trend_min_consistency=0.75,
        trend_min_edge_fraction=0.25,
        trend_min_micro_move_atr=0.03,
        forecast_low=98.0,
        forecast_median=102.0,
        forecast_high=106.0,
        stop_distance=1.5,
        target_distance=3.0,
    )


def test_ensemble_stays_inactive_until_all_roles_exist(tmp_path: Path) -> None:
    coordinator = EnsembleCoordinator(tmp_path)
    payload, features = coordinator.assess(_market(), _decision())

    assert payload["ensemble_ready"] == 0
    assert payload["ensemble_active"] == 0
    assert payload["decision"] == "WAIT"
    assert payload["base_decision"] == "WAIT"
    assert payload["regime_probability"] == -1.0
    assert payload["news_probability"] == -1.0
    assert payload["risk_model_ready"] == 0
    assert payload["risk_probability"] == -1.0
    assert payload["risk_multiplier"] == 1.0
    assert set(features) == {"regime", "entry", "news", "meta_base"}


def test_risk_multiplier_is_bounded_and_neutral_at_half_probability() -> None:
    assert probability_to_risk_multiplier(0.0) == 0.5
    assert probability_to_risk_multiplier(0.25) == 0.5
    assert probability_to_risk_multiplier(0.5) == 1.0
    assert probability_to_risk_multiplier(0.75) == 1.5
    assert probability_to_risk_multiplier(1.0) == 1.5
