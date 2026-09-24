from __future__ import annotations

from dataclasses import replace
import json
from math import log
from pathlib import Path
import sqlite3

import pytest

from ramon.bundles import activate_bundle, load_active_bundle, stage_bundle
from ramon.ensemble import (
    BinaryLogisticModel, EnsembleCoordinator, ENTRY_FEATURES, META_FEATURES,
    META_BASE_FEATURES, REGIME_FEATURES, RISK_FEATURES, probability_to_risk_multiplier,
)
from ramon.news import NEWS_FEATURES
from ramon.history import ensure_history_db, persist_trade_outcome
from ramon.train_roles import (
    Example, load_trade_examples, promotion_gate, temporal_windows, train_bundle,
    _regime_dataset,
)
from ramon.core import Bar
from test_ensemble import _decision, _market


def constant_model(names: tuple[str, ...], probability: float) -> BinaryLogisticModel:
    n = len(names)
    return BinaryLogisticModel(names, (0.0,) * n, (1.0,) * n, (0.0,) * n,
                               log(probability / (1 - probability)), {})


def bundle(root: Path, probability: float = 0.5, **metadata) -> str:
    models = {role: constant_model(names, probability) for role, names in
              (("regime", REGIME_FEATURES), ("entry", ENTRY_FEATURES),
               ("news", NEWS_FEATURES), ("meta", META_FEATURES),
               ("risk", RISK_FEATURES))}
    return stage_bundle(root, models, {"chronos_model": "test/model", "symbol": "XAUUSD_l",
                                      "trade_threshold": 0.65, "promotion_gate_passed": True, **metadata})


@pytest.mark.parametrize("probability,base,expected", [(0.5, "BUY", "WAIT"), (0.64, "SELL", "WAIT"),
                                                        (0.9, "WAIT", "BUY")])
def test_meta_is_final_authority(tmp_path, probability, base, expected):
    version = bundle(tmp_path, probability)
    activate_bundle(tmp_path, version)
    coordinator = EnsembleCoordinator(tmp_path, "test/model")
    result, _ = coordinator.assess(_market(), replace(_decision(), decision=base))
    assert result["decision"] == expected
    assert result["edge"] == (0.7 if expected == "BUY" else 0)
    assert "base_trade" not in META_FEATURES


def test_high_meta_cannot_override_invalid_edge_or_symbol(tmp_path):
    activate_bundle(tmp_path, bundle(tmp_path, 0.95))
    coordinator = EnsembleCoordinator(tmp_path, "test/model")
    decision = replace(_decision(), buy_edge=0, sell_edge=0, reason="spread_or_atr")
    assert coordinator.assess(_market(), decision)[0]["decision"] == "WAIT"
    assert coordinator.assess(replace(_market(), symbol="XAUUSD_other"), _decision())[0]["decision"] == "WAIT"


def test_atomic_bundle_and_rollback(tmp_path):
    first, second = bundle(tmp_path, 0.7), bundle(tmp_path, 0.8)
    activate_bundle(tmp_path, first)
    activate_bundle(tmp_path, second)
    assert json.loads((tmp_path / "active.json").read_text())["previous_bundle_id"] == first
    activate_bundle(tmp_path, first)
    assert load_active_bundle(tmp_path)[0]["bundle_id"] == first
    rejected = bundle(tmp_path, promotion_gate_passed=False)
    with pytest.raises(ValueError, match="promotion"):
        activate_bundle(tmp_path, rejected)
    assert load_active_bundle(tmp_path)[0]["bundle_id"] == first


def test_corrupt_partial_or_mismatched_bundle_blocks_orders(tmp_path):
    first = bundle(tmp_path)
    activate_bundle(tmp_path, first)
    coordinator = EnsembleCoordinator(tmp_path, "changed/chronos")
    assert not coordinator.ready
    assert coordinator.assess(_market(), replace(_decision(), decision="BUY"))[0]["decision"] == "WAIT"
    (tmp_path / "versions" / first / "entry.json").write_text('{}')
    coordinator = EnsembleCoordinator(tmp_path, "test/model")
    assert not coordinator.ready
    assert "checksum" in coordinator.error
    second = bundle(tmp_path)
    (tmp_path / "versions" / second / "meta.json").unlink()
    with pytest.raises(OSError):
        activate_bundle(tmp_path, second)
    assert json.loads((tmp_path / "active.json").read_text())["bundle_id"] == first


def test_legacy_loose_models_are_never_mixed_into_new_bundle(tmp_path):
    for role in ("regime", "entry", "meta"):
        (tmp_path / f"{role}.json").write_text('{}')
    coordinator = EnsembleCoordinator(tmp_path)
    assert not coordinator.ready
    assert coordinator.status()["ensemble_mode"] == "bootstrap_chronos"


def test_temporal_windows_purge_future_labels_and_duplicate_times():
    rows = [Example(1000 + i * 30, 1000 + i * 30 + 900, {}, i % 2) for i in range(500)]
    base, meta, valid = temporal_windows(rows)
    assert max(r.label_end for r in base) < min(r.time for r in meta)
    assert max(r.label_end for r in meta) < min(r.time for r in valid)
    assert not ({r.time for r in base} & {r.time for r in meta})
    # Regime labels end at close of the eighth future bar, not at its opening.
    bars = tuple(Bar(i * 900 + 900, 100, 101, 99, 100) for i in range(60))
    regime = _regime_dataset(bars)
    assert regime[0].label_end - regime[0].time == 8 * 900


def test_promotion_rejects_regression_even_when_accuracy_is_high():
    candidate = dict(trades=30, net_r=3.0, max_drawdown_r=2.0, balanced_accuracy=0.8, brier=0.1)
    incumbent = dict(trades=30, net_r=5.0, max_drawdown_r=2.0, brier=0.09)
    reasons = promotion_gate(candidate, incumbent, incumbent, minimum_trades=20,
                             improvement=0.5, maximum_drawdown=8)
    assert "no_net_r_improvement_over_incumbent" in reasons
    assert "probability_quality_worse_than_incumbent" in reasons


def seed_trade(db: Path, index: int, *, chronos_model="test/model", schema=3) -> Example:
    time = 1_800_000_000 + index * 7200
    positive = index % 2
    x = 2.0 if positive else 0.2
    key = f"{index:016x}"
    features = {"regime": {name: x for name in REGIME_FEATURES},
                "entry": {name: x for name in ENTRY_FEATURES},
                "news": {name: x for name in NEWS_FEATURES},
                "meta_base": {name: x for name in META_BASE_FEATURES}}
    with sqlite3.connect(db) as conn:
        conn.execute("""INSERT INTO decision_samples
            (captured,symbol,signal_bar_time,mid,spread,atr,direction,base_decision,
             regime_features,entry_features,news_features,meta_base_features,sample_key,chronos_model,
             schema_version,quote_time,final_decision)
             VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
             (time, "XAUUSD_l", time-900, 100, .1, 1, "BUY", "BUY",
              json.dumps(features["regime"]), json.dumps(features["entry"]),
              json.dumps(features["news"]), json.dumps(features["meta_base"]),
              key, chronos_model, schema, time, "BUY"))
    payload = {"sample_key": key, "trade_key": f"real-account:{index}", "symbol": "XAUUSD_l",
               "direction": "BUY", "opened": time + 1, "closed": time + 900,
               "net_units": 10 if positive else -10, "initial_risk_units": 10,
               "exit_reason": "DEAL_REASON_TP" if positive else "DEAL_REASON_SL"}
    persist_trade_outcome(db, payload, time + 1000)
    persist_trade_outcome(db, payload, time + 1001)  # broker replay after restart is idempotent
    return Example(time, time + 900, features, positive, 1.0 if positive else -1.0, True, "BUY",
                   "DEAL_REASON_TP" if positive else "DEAL_REASON_SL")


def test_trade_join_deduplicates_and_excludes_legacy_wrong_model_and_direction(tmp_path):
    db = ensure_history_db(tmp_path / "history.db")
    seed_trade(db, 1)
    seed_trade(db, 2, schema=1)
    seed_trade(db, 3, chronos_model="old/model")
    seed_trade(db, 4)
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE trade_outcomes SET direction='SELL' WHERE sample_key=?", (f"{4:016x}",))
        assert conn.execute("SELECT COUNT(*) FROM trade_outcomes").fetchone()[0] == 4
    rows = load_trade_examples(db, "XAUUSD_l", "test/model")
    assert len(rows) == 1
    assert rows[0].net_r == 1.0


def test_closed_loss_label_is_not_replaced_by_later_price_recovery(tmp_path):
    db = ensure_history_db(tmp_path / "history.db")
    seed_trade(db, 2)  # actual SL / negative net profit
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE decision_samples SET mid=100000")
    row = load_trade_examples(db, "XAUUSD_l", "test/model")[0]
    assert row.label == 0
    assert row.net_r == -1.0


def test_full_training_promotes_frozen_roles_then_requires_fresh_holdout(tmp_path, monkeypatch):
    import ramon.train_roles as training
    db = ensure_history_db(tmp_path / "history.db")
    examples = [seed_trade(db, i + 1) for i in range(300)]
    # Known causal binary regime fixture; all future-label times are preserved.
    regime = [replace(row, label_end=row.time + 7200) for row in examples]
    monkeypatch.setattr(training, "load_bars", lambda *args: ((), ()))
    monkeypatch.setattr(training, "_regime_dataset", lambda bars: regime)
    root = tmp_path / "roles"
    report = train_bundle(db=db, symbol="XAUUSD_l", chronos_model="test/model", out=root,
                          minimum_samples=200, regime_minimum=40, minimum_trades=20)
    assert report["status"] == "promoted", report
    manifest, models = load_active_bundle(root, "test/model")
    assert manifest["base_label_end"] < manifest["meta_start"]
    assert manifest["regime_label_end"] < manifest["meta_start"]
    assert manifest["training_label_end"] < manifest["holdout_start"]
    assert models["entry"].metadata["last_label_end"] < manifest["meta_start"]
    assert models["news"].metadata["last_label_end"] < manifest["meta_start"]
    assert models["meta"].metadata["last_label_end"] < manifest["holdout_start"]
    assert models["risk"].metadata["last_label_end"] < manifest["meta_start"]
    assert report["candidate"]["risk_balanced_accuracy"] >= 0.52
    assert report["candidate"]["net_r"] > report["chronos_baseline"]["net_r"]
    again = train_bundle(db=db, symbol="XAUUSD_l", chronos_model="test/model", out=root,
                         minimum_samples=200, regime_minimum=40)
    assert again["status"] == "waiting_for_fresh_holdout"


def test_bad_model_vectors_are_rejected(tmp_path):
    path = tmp_path / "model.json"
    constant_model(("x",), 0.9).save(path)
    raw = json.loads(path.read_text())
    raw["weights"] = []
    path.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="length"):
        BinaryLogisticModel.load(path)


def test_role_training_excludes_manual_exit_labels(tmp_path):
    db = ensure_history_db(tmp_path / "history.db")
    seed_trade(db, 1)
    time = 1_800_000_000 + 1 * 7200
    persist_trade_outcome(
        db,
        {
            "sample_key": f"{1:016x}",
            "trade_key": "real-account:1",
            "symbol": "XAUUSD_l",
            "direction": "BUY",
            "opened": time + 1,
            "closed": time + 900,
            "net_units": 10,
            "initial_risk_units": 10,
            "exit_reason": "DEAL_REASON_CLIENT",
        },
        time + 1002,
    )
    assert load_trade_examples(db, "XAUUSD_l", "test/model") == []


def test_full_stop_probability_reduces_risk_multiplier():
    assert probability_to_risk_multiplier(0.10, target="full_stop_loss") == 1.5
    assert probability_to_risk_multiplier(0.50, target="full_stop_loss") == 1.0
    assert probability_to_risk_multiplier(0.80, target="full_stop_loss") == 0.5
    # Backward compatibility for already-active risk bundles.
    assert probability_to_risk_multiplier(0.80, target="win") == 1.5


def test_trade_examples_preserve_full_stop_provenance(tmp_path):
    db = ensure_history_db(tmp_path / "history.db")
    seed_trade(db, 1)
    seed_trade(db, 2)
    rows = load_trade_examples(db, "XAUUSD_l", "test/model")
    assert {row.exit_reason for row in rows} == {"DEAL_REASON_TP", "DEAL_REASON_SL"}
