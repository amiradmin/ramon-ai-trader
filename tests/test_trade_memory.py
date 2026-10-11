import sqlite3

from ramon.history import ensure_history_db
from ramon.trade_memory import summarize


def test_trade_memory_is_read_only_and_marks_small_cells_ineligible(tmp_path):
    db = tmp_path / "ramon.sqlite3"
    ensure_history_db(db)
    with sqlite3.connect(db) as con:
        con.execute("""INSERT INTO trade_outcomes
          (trade_key,sample_key,symbol,direction,opened,closed,net_units,initial_risk_units,net_r,exit_reason,received,training_status)
          VALUES ('t1','s1','XAUUSD_l','BUY',1800000000,1800000900,1,1,1,'DEAL_REASON_TP',1,'LEARNABLE')""")
    report = summarize(db, minimum_samples=2)
    assert report["learnable_outcomes"] == 1
    assert report["cells"][0]["eligible_for_shadow"] == 0
