import json
import sqlite3

from ramon.entry_audit import audit
from ramon.history import ensure_history_db


def test_entry_audit_respects_chronology_and_does_not_write(tmp_path):
    db = tmp_path / "history.sqlite3"
    ensure_history_db(db)
    with sqlite3.connect(db) as con:
        for i in range(10):
            key = f"sample-{i}"
            con.execute("""INSERT INTO decision_samples
                (captured,symbol,signal_bar_time,mid,spread,atr,direction,
                 base_decision,regime_features,entry_features,meta_base_features,sample_key)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (i + 1, "XAUUSD_l", i + 1, 100, 0.4, 1, "BUY", "BUY", "{}",
                 json.dumps({"signal_strength": 0.3 if i % 2 else 0.1}), "{}", key))
            con.execute("""INSERT INTO trade_outcomes
                (trade_key,sample_key,symbol,direction,opened,closed,net_units,
                 initial_risk_units,net_r,exit_reason,received)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (f"trade-{i}", key, "XAUUSD_l", "BUY", i + 1, i + 2,
                 1 if i % 2 else -1, 1, 1 if i % 2 else -1, "DEAL_REASON_SL", i + 2))
    before = db.read_bytes()
    result = audit(db, "XAUUSD_l", "signal_strength", 0.2, "above")
    assert result["earlier"]["kept"]["trades"] == 4
    assert result["later"]["kept"]["net_units"] == 1
    assert result["later"]["skipped"]["net_units"] == -1
    assert db.read_bytes() == before
