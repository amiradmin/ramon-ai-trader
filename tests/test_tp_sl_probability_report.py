import sqlite3

from ramon.history import ensure_history_db
from ramon.tp_sl_probability_report import summarize


def test_tp_sl_report_uses_only_terminal_broker_labels(tmp_path):
    db = tmp_path / "ramon.sqlite3"
    ensure_history_db(db)
    with sqlite3.connect(db) as con:
        for key, reason, closed in (("a", "DEAL_REASON_TP", 1_800_000_300), ("b", "DEAL_REASON_SL", 1_800_000_900), ("c", "DEAL_REASON_CLIENT", 1_800_000_300)):
            con.execute("""INSERT INTO trade_outcomes
              (trade_key,sample_key,symbol,direction,opened,closed,net_units,initial_risk_units,net_r,exit_reason,received,training_status)
              VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""", (key, "s" + key, "XAUUSD_l", "BUY", 1_800_000_000, closed, 1, 1, 1, reason, 1, "LEARNABLE"))
    report = summarize(db, horizons=(5, 15), minimum_samples=2)
    assert report["terminal_tp_or_sl_outcomes"] == 2
    assert report["cells"][0]["horizon_minutes"] == 5
    assert report["cells"][0]["tp_before_sl_probability"] == 1.0
    assert report["cells"][1]["tp_before_sl_probability"] == 0.5
