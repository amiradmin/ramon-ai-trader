from __future__ import annotations

import sqlite3

from ramon.history import ensure_history_db
from ramon.volatility_regime_lab import bucket_label, candidate_bucket, parse_bins, report, summarize


def seed(db, i, *, atr, net, net_r, status="LEARNABLE", mfe=None, mae=None):
    sample=f"{i:016x}"
    with sqlite3.connect(db) as con:
        con.execute(
            """INSERT INTO decision_samples
               (captured,symbol,signal_bar_time,mid,spread,atr,direction,base_decision,
                regime_features,entry_features,meta_base_features,sample_key)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (100+i, "XAUUSD_l", 90+i, 4000, .4, atr, "BUY", "BUY", "{}", "{}", "{}", sample),
        )
        con.execute(
            """INSERT INTO trade_outcomes
               (trade_key,sample_key,symbol,direction,opened,closed,net_units,initial_risk_units,
                net_r,exit_reason,received,training_status)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (f"acct:{i}", sample, "XAUUSD_l", "BUY", 100+i, 200+i, net, 10, net_r,
             "DEAL_REASON_TP" if net > 0 else "DEAL_REASON_SL", 300+i, status),
        )
        if mfe is not None and mae is not None:
            con.execute(
                """INSERT INTO target_outcomes
                   (sample_key,trade_key,symbol,direction,opened,closed,tp1,tp2,tp3,
                    tp1_hit,tp2_hit,tp3_hit,tp1_time,tp2_time,tp3_time,bars_to_tp1,bars_to_tp2,bars_to_tp3,
                    mfe_price,mae_price,mfe_atr,mae_atr,continuation_tp2,continuation_tp3,source,computed_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (sample, f"acct:{i}", "XAUUSD_l", "BUY", 100+i, 200+i, 1,2,3,
                 1,0,0,None,None,None,1,None,None,4,2,mfe,mae,0,0,"test",400+i),
            )


def test_parse_bins_and_boundaries():
    assert parse_bins("5,8,12") == (5.0, 8.0, 12.0)
    assert bucket_label(4.99, (5,8,12))[0] == "<5"
    assert bucket_label(5.0, (5,8,12))[0] == "5-8"
    assert bucket_label(12.0, (5,8,12))[0] == ">=12"


def test_summarize_uses_entry_atr_and_clean_trades_only(tmp_path):
    db=ensure_history_db(tmp_path/"h.db")
    seed(db,1,atr=4,net=10,net_r=1,mfe=1.2,mae=.2)
    seed(db,2,atr=6,net=-5,net_r=-.5,mfe=.6,mae=.8)
    seed(db,3,atr=6.5,net=15,net_r=1.5,mfe=1.8,mae=.3)
    seed(db,4,atr=13,net=99,net_r=9,status="CENSORED_MANUAL")
    payload=report(str(db),"XAUUSD_l",(5,8,12),1,as_json=True)
    by={row["label"]:row for row in payload["buckets"]}
    assert payload["joined_trades"] == 3
    assert by["<5"]["trades"] == 1
    assert by["5-8"]["trades"] == 2
    assert by["5-8"]["net_units"] == 10
    assert by["5-8"]["profit_factor"] == 3.0
    assert by["5-8"]["avg_mfe_atr"] == 1.2
    assert by["5-8"]["avg_mae_atr"] == .55
    assert payload["live_execution_changed"] is False


def test_candidate_requires_minimum_samples():
    rows=[
        {"atr":6,"net_units":2,"net_r":.2,"mfe_atr":None,"mae_atr":None},
        {"atr":9,"net_units":5,"net_r":.5,"mfe_atr":None,"mae_atr":None},
    ]
    stats=summarize(rows,(5,8,12))
    assert candidate_bucket(stats,2) is None
    assert candidate_bucket(stats,1).label == "8-12"
