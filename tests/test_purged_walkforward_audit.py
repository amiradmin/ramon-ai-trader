import pytest

from ramon.purged_walkforward_audit import chronological_folds


def sample(i, end_delay=5):
    return {"sample_key": f"s{i}", "decision_utc": 100 + i * 10,
            "label_end_utc": 100 + i * 10 + end_delay}


def test_no_label_crosses_embargo_or_test_boundary():
    rows = [sample(i) for i in range(24)]
    result = chronological_folds(rows, folds=3, embargo_seconds=20)
    lookup = {r["sample_key"]: r for r in rows}
    assert len(result["folds"]) == 3
    for fold in result["folds"]:
        assert set(fold["training_keys"]).isdisjoint(fold["test_keys"])
        assert fold["training_count"] > 0
        for key in fold["training_keys"]:
            assert lookup[key]["label_end_utc"] < fold["effective_train_label_end_before_utc"]
        assert max(int(lookup[k]["decision_utc"]) for k in fold["training_keys"]) < fold["test_start_utc"]


def test_purges_overlapping_long_horizon_sample():
    rows = [sample(i) for i in range(16)]
    rows[1]["label_end_utc"] = 99999
    result = chronological_folds(rows, folds=2, embargo_seconds=0)
    for fold in result["folds"]:
        assert "s1" not in fold["training_keys"]


def test_reject_duplicate_or_invalid_data():
    with pytest.raises(ValueError):
        chronological_folds([sample(1), sample(1)] * 4)
    with pytest.raises(ValueError):
        chronological_folds([sample(i) for i in range(4)], folds=3)
    with pytest.raises(ValueError):
        chronological_folds([sample(i) for i in range(12)], embargo_seconds=-1)
