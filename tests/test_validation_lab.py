from ramon.validation_lab import (
    Sample,
    ambiguous_fraction,
    bootstrap_mean_ci,
    evaluate,
    metrics,
    multi_seed_random_distribution,
    paired_mean_r_deltas,
    replay_direction,
    split_holdout,
    walk_forward_folds,
)


def bars(*rows):
    return [
        {"time": t, "open": o, "high": h, "low": l, "close": c}
        for t, o, h, l, c in rows
    ]


def sample(**changes):
    base = dict(
        captured=1000,
        signal_bar_time=900,
        entry_time=1000,
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
        sample(captured=1, signal_bar_time=900, entry_time=1000, direction="BUY"),
        sample(captured=2, signal_bar_time=1800, entry_time=1900, direction="SELL"),
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
        sample(captured=i, signal_bar_time=i * 900, entry_time=i * 900 + 100, direction="BUY")
        for i in range(1, 11)
    ]
    train, test = split_holdout(rows, 0.30)
    assert len(train) == 7
    assert len(test) == 3
    assert train[-1].entry_time < test[0].entry_time

    m = metrics([
        type("R", (), {"outcome_r": 2.0})(),
        type("R", (), {"outcome_r": -1.0})(),
    ])
    assert m["trades"] == 2
    assert m["win_rate"] == 50.0
    assert m["mean_r"] == 0.5
    assert m["net_r"] == 1.0
    assert m["pf_r"] == 2.0



def test_walk_forward_folds_are_chronological_and_purged():
    rows = [
        sample(captured=i, signal_bar_time=i * 900, entry_time=i * 900 + 100)
        for i in range(1, 31)
    ]
    folds = walk_forward_folds(
        rows,
        folds=3,
        max_bars=2,
        embargo_bars=1,
        initial_fraction=0.40,
    )
    assert len(folds) == 3
    for fold in folds:
        assert fold.test
        assert all(
            dev.entry_time + (2 + 1) * 900 < fold.test[0].entry_time
            for dev in fold.development
        )
        assert list(fold.test) == sorted(fold.test, key=lambda row: row.entry_time)


def test_bootstrap_mean_ci_is_deterministic_and_contains_sample_mean():
    values = [1.0, 0.5, -0.5, 1.5, 0.0]
    one = bootstrap_mean_ci(values, iterations=500, seed=7)
    two = bootstrap_mean_ci(values, iterations=500, seed=7)
    assert one == two
    mean, low, high = one
    assert mean == sum(values) / len(values)
    assert low <= mean <= high


def test_paired_delta_compares_same_entries():
    samples = [
        sample(captured=1, entry_time=1000, signal_bar_time=900, direction="BUY"),
        sample(captured=2, entry_time=1900, signal_bar_time=1800, direction="SELL"),
    ]
    data = bars(
        (900, 100, 101, 99, 100),
        (1800, 100, 105, 99, 104),
        (2700, 104, 106, 98, 99),
        (3600, 99, 103, 97, 102),
        (4500, 102, 104, 96, 97),
    )
    deltas = paired_mean_r_deltas(
        samples,
        data,
        baseline="random",
        max_bars=2,
        seed=42,
    )
    assert len(deltas) == 2



def test_multi_seed_random_distribution_is_deterministic():
    samples = [
        sample(captured=1, entry_time=1000, signal_bar_time=900, direction="BUY"),
        sample(captured=2, entry_time=1900, signal_bar_time=1800, direction="SELL"),
    ]
    data = bars(
        (900, 100, 101, 99, 100),
        (1800, 100, 105, 99, 104),
        (2700, 104, 106, 98, 99),
        (3600, 99, 103, 97, 102),
        (4500, 102, 104, 96, 97),
    )
    one = multi_seed_random_distribution(samples, data, max_bars=2, seeds=100, seed_base=11)
    two = multi_seed_random_distribution(samples, data, max_bars=2, seeds=100, seed_base=11)
    assert one == two
    random_means, ramon_mean, percentile = one
    assert len(random_means) == 100
    assert -10 < ramon_mean < 10
    assert 0 <= percentile <= 100


def test_ambiguous_fraction_counts_same_bar_tp_and_sl_touch():
    samples = [sample(entry_time=1000, signal_bar_time=900, direction="BUY")]
    data = bars(
        (900, 100, 101, 99, 100),
        (1800, 100, 105, 97, 104),
    )
    ambiguous, total, pct = ambiguous_fraction(
        samples,
        data,
        direction_mode="ramon",
        max_bars=2,
        seed=1,
    )
    assert (ambiguous, total, pct) == (1, 1, 100.0)
