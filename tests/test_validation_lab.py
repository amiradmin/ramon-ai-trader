from ramon.validation_lab import Sample, evaluate, metrics, replay_direction, split_holdout


def bars(*rows):
    return [
        {"time": t, "open": o, "high": h, "low": l, "close": c}
        for t, o, h, l, c in rows
    ]


def sample(**changes):
    base = dict(
        captured=1000,
        signal_bar_time=900,
        direction="BUY",
        mid=100.0,
        spread=0.4,
        stop_distance=2.0,
        target_distance=4.0,
    )
    base.update(changes)
    return Sample(**base)


def test_replay_uses_same_stored_sl_tp_and_is_conservative_on_ambiguous_bar():
    s = sample()
    data = bars(
        (900, 100, 101, 99, 100),
        (1800, 100, 105, 97, 104),  # touches BUY TP and SL
    )
    result = replay_direction(data, s, "BUY", max_bars=4)
    assert result is not None
    assert result.exit_kind == "SL"
    assert result.outcome_r == -1.0


def test_replay_target_reward_is_stored_rr():
    s = sample(stop_distance=2.0, target_distance=6.0)
    data = bars(
        (900, 100, 101, 99, 100),
        (1800, 100, 106.5, 99, 106),
    )
    result = replay_direction(data, s, "BUY", max_bars=4)
    assert result is not None
    assert result.exit_kind == "TP"
    assert result.outcome_r == 3.0


def test_evaluate_baselines_are_deterministic_and_keep_same_exit_geometry():
    samples = [
        sample(captured=1, signal_bar_time=900, direction="BUY"),
        sample(captured=2, signal_bar_time=1800, direction="SELL"),
    ]
    data = bars(
        (900, 100, 101, 99, 100.5),
        (1800, 100.5, 105, 100, 104),
        (2700, 104, 105, 98, 99),
        (3600, 99, 103, 97, 102),
    )
    one = evaluate(samples, data, max_bars=2, random_seed=42)
    two = evaluate(samples, data, max_bars=2, random_seed=42)
    assert [(r.direction, r.outcome_r) for r in one["random"]] == [
        (r.direction, r.outcome_r) for r in two["random"]
    ]
    assert len(one["ramon"]) == 2
    assert len(one["always_buy"]) == 2
    assert len(one["always_sell"]) == 2


def test_metrics_and_temporal_holdout():
    rows = [
        sample(captured=i, signal_bar_time=i * 900, direction="BUY")
        for i in range(1, 11)
    ]
    train, test = split_holdout(rows, 0.30)
    assert len(train) == 7
    assert len(test) == 3
    assert train[-1].signal_bar_time < test[0].signal_bar_time

    m = metrics([
        type("R", (), {"outcome_r": 2.0})(),
        type("R", (), {"outcome_r": -1.0})(),
    ])
    assert m["trades"] == 2
    assert m["win_rate"] == 50.0
    assert m["mean_r"] == 0.5
    assert m["net_r"] == 1.0
    assert m["pf_r"] == 2.0
