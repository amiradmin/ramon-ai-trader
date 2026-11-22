"""Two additional offline research estimators for Ramon replay history.

Train on historical *simulated* labels only. Neither model is eligible to
change decisions, sizing, risk limits, strategy order or execution.
"""
from __future__ import annotations

import hashlib
import io
import math
from collections import Counter
from datetime import datetime, timezone

from .replay_ml import make_features, replay_training_rows

MODEL_SPECS = {
    "replay_trade_success": ("Replay TP-before-SL · Random Forest", "target_before_stop"),
    "replay_market_regime": ("Forward four-bar regime · Random Forest", "forward_4bar_regime"),
}


def regime_rows(bars, *, horizon=4):
    """Future price labels for research, with predictors strictly at signal time.

    Classes describe forward close movement measured against current ATR:
    UP, DOWN, or RANGE. These are *future outcomes*, not instantaneous ADX
    regime or a production-ready market classifier.
    """
    items = []
    for i in range(59, len(bars)-horizon):
        current, future = bars[i], bars[i+horizon]
        # Avoid labels spanning session gaps or missing candles.
        if any(int(bars[j+1]["time"])-int(bars[j]["time"]) != 900
               for j in range(i, i+horizon)):
            continue
        ts = int(current["time"])
        features = make_features(bars[:i+1], signal_at=ts, setup="MARKET",
                                 side="BUY", spread_points=0)
        atr = features["snapshot_atr"]
        move = (float(future["close"])-float(current["close"]))/atr
        label = "UP" if move > 0.6 else "DOWN" if move < -0.6 else "RANGE"
        # Remove constant synthetic setup and side tags when predicting regime.
        features = {k: v for k, v in features.items() if not k.startswith("tag_")}
        items.append((ts, features, label))
    return items


def _chronological_partition(rows, *, horizon_seconds=0):
    if len(rows) < 60:
        raise ValueError(f"Insufficient observations: {len(rows)}; need at least 60")
    times = sorted({r[0] for r in rows})
    if len(times) < 30:
        raise ValueError("Need at least 30 distinct timestamps")
    boundary = times[int(0.8*len(times))]
    # Purge training samples whose outcomes could reach into holdout.
    training = [r for r in rows if r[0] + horizon_seconds < boundary]
    testing = [r for r in rows if r[0] >= boundary]
    if len(training) < 40 or len(testing) < 12:
        raise ValueError("Need at least 40 training and 12 held-out observations")
    return training, testing, boundary


def train_classifier(model_key, report, bars):
    """Train and holdout-validate one model, returning serialized research artifact."""
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.feature_extraction import DictVectorizer
    from sklearn.metrics import accuracy_score, brier_score_loss, log_loss
    import joblib

    if model_key not in MODEL_SPECS:
        raise ValueError("Unknown replay classifier")
    if report.get("mode") != "ISOLATED_STRATEGY_RESEARCH_ESTIMATE":
        raise ValueError("Replay research report required")
    if (report.get("data_quality") or {}).get("excluded_fraction", 1) > 0.05:
        raise ValueError("Replay history has excessive invalid M15 timestamps")
    if model_key == "replay_trade_success":
        # This checks the replay fingerprint and the source quality.
        original = replay_training_rows(report, bars)
        # STOP/TARGET are the simulator's own mutually exclusive fills.
        rows = [(ts, features, int(r > 0)) for ts, features, r in original]
        horizon_seconds = 0  # See outcome-time embargo below.
        # Prevent overlapping outcome leakage: exclude training trades not
        # settled before the holdout starts.
        closed = {}
        for strategy in report["strategies"]:
            for trade in strategy.get("trades", []):
                if trade.get("reason") in ("STOP", "TARGET"):
                    key = (int(trade["signal_at"]), str(strategy["setup"]), str(trade["side"]))
                    closed.setdefault(key, []).append(int(trade["closed_at"]))
        # Assign a closed timestamp to each trade, keeping duplicates safe.
        detailed = []
        for ts, features, label in rows:
            key = (ts, features["tag_setup_type"], features["tag_side"])
            timestamps = closed.get(key)
            if not timestamps:
                raise ValueError("Replay close timestamp missing")
            detailed.append((ts, features, label, timestamps.pop(0)))
        train, test, boundary = _chronological_partition(rows)
        train = [r for r in train if any(
            x[0] == r[0] and x[1] is r[1] and x[3] < boundary for x in detailed)]
        if len(train) < 40:
            raise ValueError("Fewer than 40 trades closed before test period; avoid outcome leakage")
    else:
        rows = regime_rows(bars)
        # Four complete candles of forward label horizon are purged at boundary.
        train, test, boundary = _chronological_partition(rows, horizon_seconds=4*900)

    y_train = [r[2] for r in train]
    y_test = [r[2] for r in test]
    classes = sorted(set(y_train))
    if len(classes) < 2 or any(y_train.count(cls) < 8 for cls in classes):
        raise ValueError("Training labels lack class diversity (>=8 per observed class)")
    if set(y_test) - set(y_train):
        raise ValueError("Holdout contains previously unseen class")
    vectorizer = DictVectorizer(sparse=False, sort=True)
    x_train = vectorizer.fit_transform([r[1] for r in train])
    x_test = vectorizer.transform([r[1] for r in test])
    model = RandomForestClassifier(n_estimators=160, min_samples_leaf=4,
                                   max_features=0.8, random_state=42, n_jobs=1)
    model.fit(x_train, y_train)
    proba = model.predict_proba(x_test)
    observed = model.classes_.tolist()
    prior = [y_train.count(cls)/len(y_train) for cls in observed]
    if model_key == "replay_trade_success":
        if set(observed) != {0, 1}:
            raise ValueError("Training data must include both STOP and TARGET")
        positive_index = observed.index(1)
        actual = [int(v) for v in y_test]
        brier = float(brier_score_loss(actual, proba[:, positive_index]))
        base = float(brier_score_loss(actual, [prior[positive_index]]*len(actual)))
        score = brier
        baseline = base
        metric_name = "brier_loss"
        # A classifier cannot claim calibration from this holdout alone.
    else:
        score = float(log_loss(y_test, proba, labels=observed))
        baseline = float(log_loss(y_test, [prior]*len(y_test), labels=observed))
        metric_name = "multiclass_log_loss"
    passed = math.isfinite(score) and math.isfinite(baseline) and score < baseline
    accuracy = float(accuracy_score(y_test, model.predict(x_test)))
    if passed:
        vectorizer = DictVectorizer(sparse=False, sort=True)
        model = RandomForestClassifier(n_estimators=160, min_samples_leaf=4,
                                       max_features=0.8, random_state=42, n_jobs=1)
        model.fit(vectorizer.fit_transform([r[1] for r in rows]),
                  [r[2] for r in rows])
    buf = io.BytesIO()
    target = MODEL_SPECS[model_key][1]
    joblib.dump({"model_key": model_key, "target": target,
                 "feature_names": list(vectorizer.feature_names_),
                 "vectorizer": vectorizer, "estimator": model,
                 "classes": model.classes_.tolist()}, buf)
    raw = buf.getvalue()
    return {
        "metrics": {
            "observations": len(rows), "training_observations": len(train),
            "holdout_observations": len(test), "test_start_utc":
                datetime.fromtimestamp(boundary, tz=timezone.utc).isoformat(),
            metric_name: round(score, 8), "baseline_"+metric_name: round(baseline, 8),
            "holdout_accuracy": round(accuracy, 5),
            "training_classes": {str(k): v for k, v in Counter(y_train).items()},
            "holdout_classes": {str(k): v for k, v in Counter(y_test).items()},
            "training_source": "SIMULATED_REPLAY_NOT_BROKER_EXECUTION",
            "history_fingerprint": report["data_quality"]["history_fingerprint"],
        },
        "validation": {"passed": bool(passed),
            "checks": {"chronological_holdout": True,
                       "outcome_leakage_embargo": True,
                       "beats_constant_baseline_on_proper_scoring_rule": bool(passed)},
            "note": "Research only. No calibrated live probabilities, no trading authority."},
        "version": "replay-" + hashlib.sha256(raw).hexdigest()[:16],
        "artifact_bytes": raw,
    }
