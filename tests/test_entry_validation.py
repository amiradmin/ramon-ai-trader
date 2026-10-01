from dataclasses import replace

from ramon.ensemble import ENTRY_FEATURES
from ramon.entry_validation import validate_entry
from ramon.train_roles import Example
from ramon.train_roles import read_trade_examples


def examples(count=240):
    return [Example(1000+i*100, 1050+i*100,
                    {"entry": {name: (2.0 if i % 2 else .2) for name in ENTRY_FEATURES}},
                    i % 2, 1 if i % 2 else -1, True, "BUY", "DEAL_REASON_TP")
            for i in range(count)]


def test_waits_without_lowering_sample_gate_or_promoting():
    report = validate_entry(examples(30))
    assert report["status"] == "waiting_for_data"
    assert report["promotion_allowed"] is False


def test_overlapping_labels_are_purged_and_models_do_not_see_test(monkeypatch):
    import ramon.entry_validation as module
    original = module.fit_role
    seen = []
    def spy(rows, *args):
        seen.extend(rows)
        return original(rows, *args)
    monkeypatch.setattr(module, "fit_role", spy)
    rows = examples()
    rows[119] = replace(rows[119], label_end=rows[180].time+1)
    report = validate_entry(rows)
    assert report["windows"]["purged"] == 1
    assert max(r.label_end for r in seen) < report["windows"]["calibration_start_mt5"]
    assert report["calibrated_test"]["samples"] == 60
    assert report["selected_threshold"] is None


def test_test_labels_do_not_change_probabilities_or_thresholds():
    rows = examples()
    first = validate_entry(rows)
    mutated = rows[:180] + [replace(r, label=1-r.label, net_r=-r.net_r) for r in rows[180:]]
    second = validate_entry(mutated)
    for name in ("raw_test", "calibrated_test"):
        assert [b["mean_probability"] for b in first[name]["reliability_bins"]] == [
            b["mean_probability"] for b in second[name]["reliability_bins"]]
        assert first[name]["brier"] != second[name]["brier"]
    assert first["thresholds_fixed_before_test"] == second["thresholds_fixed_before_test"]


def test_insufficient_calibration_class_counts_are_explicit():
    rows = examples(120)
    report = validate_entry(rows)
    assert "calibrated_test" not in report
    assert "unavailable" in report["calibration_status"]
    assert report["raw_test"]["samples"] == 30


def test_offline_selection_excludes_manual_expert_without_changing_live_default(tmp_path):
    import sqlite3
    from ramon.history import ensure_history_db
    from test_role_learning import seed_trade
    db = ensure_history_db(tmp_path / "history.db")
    for i in range(1, 4):
        seed_trade(db, i)
    with sqlite3.connect(db) as connection:
        for i, detail in ((1, "manual_close"), (2, "manual_dashboard_close")):
            connection.execute("UPDATE trade_outcomes SET exit_reason='DEAL_REASON_EXPERT',exit_detail=? WHERE sample_key=?", (detail, f"{i:016x}"))
        assert len(read_trade_examples(connection, "XAUUSD_l", "test/model")) == 3
        assert len(read_trade_examples(connection, "XAUUSD_l", "test/model", exclude_manual_expert=True)) == 1
