from __future__ import annotations

import json
import sqlite3

from ramon.history import ensure_history_db
from ramon.news_event_audit import audit


def test_news_audit_separates_untraded_waits_from_real_outcomes(tmp_path) -> None:
    db = ensure_history_db(tmp_path / "ramon.sqlite3")
    with sqlite3.connect(db) as conn:
        for index, (phase, decision, side, outcome) in enumerate((
            ("NORMAL", "BUY", "BUY", -0.5),
            ("POST_RELEASE", "WAIT", "BUY", None),
            ("POST_RELEASE", "SELL", "SELL", 1.0),
        )):
            sample = f"{index:016x}"
            conn.execute("""INSERT INTO decision_samples
                (captured,symbol,signal_bar_time,mid,spread,atr,direction,
                 base_decision,regime_features,entry_features,meta_base_features,
                 quote_time,final_decision,sample_key,news_features,model_metadata)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                    1000+index, "XAUUSD_l", 900, 100, 0.2, 2, side,
                    decision, "{}", "{}", "{}", 1000+index, decision, sample,
                    json.dumps({"post_high_30m": float(phase == "POST_RELEASE"),
                                "signed_surprise": -0.2, "event_inflation": 1.0}),
                    json.dumps({"news_live_context": {"news_phase": phase}}),
                ))
            if outcome is not None:
                conn.execute("""INSERT INTO trade_outcomes
                    (trade_key,sample_key,symbol,direction,opened,closed,net_units,
                     initial_risk_units,net_r,exit_reason,received,training_status)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""", (
                        f"trade-{index}", sample, "XAUUSD_l", side, 1001+index,
                        1100+index, outcome * 10, 10, outcome, "DEAL_REASON_TP", 1200,
                        "LEARNABLE",
                    ))

    result = audit(db)
    assert result["all"]["snapshots"] == 3
    assert result["all"]["executed_trades"] == 2
    assert result["phases"]["POST_RELEASE"]["base_wait"] == 1
    assert result["phases"]["POST_RELEASE"]["net_r_executed"] == 1.0
    assert result["post_release_event_types"]["inflation"]["executed_trades"] == 1
    assert result["preliminary_counts_available"] is False
