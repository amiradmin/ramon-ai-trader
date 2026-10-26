import json
import sqlite3
from datetime import datetime, timezone

from ramon.daily_signal_execution_audit import audit


def test_audit_does_not_claim_execution_or_profit(tmp_path):
    p=tmp_path/"r.sqlite3"
    ts=int(datetime(2026,10,12,12,tzinfo=timezone.utc).timestamp())
    with sqlite3.connect(p) as con:
        con.executescript("""
        CREATE TABLE decision_samples(id INTEGER PRIMARY KEY,captured INTEGER,symbol TEXT,
        signal_bar_time INTEGER,direction TEXT,final_decision TEXT,model_metadata TEXT,sample_key TEXT);
        CREATE TABLE trade_outcomes(sample_key TEXT,trade_key TEXT,direction TEXT,opened INTEGER,
        closed INTEGER,symbol TEXT,entry_source TEXT,entry_magic INTEGER,entry_ea_version TEXT,
        actual_fill_price REAL);
        CREATE TABLE history_bars(symbol TEXT,timeframe TEXT,time INTEGER,close REAL);
        """)
        for i, direction, decision, reason in [
            (0,"SELL","WAIT","ai_engine_v2_unconfirmed_base_wait"),
            (1,"SELL","WAIT","ai_engine_v2_market_hazard"),
            (2,"SELL","SELL","confirmed"),
        ]:
            meta={"decision_audit":{"final":{"decision":decision,"reason":reason}}}
            con.execute("INSERT INTO decision_samples(captured,symbol,signal_bar_time,direction,final_decision,model_metadata,sample_key) VALUES(?,?,?,?,?,?,?)",
                        (ts+5*i,"XAUUSD_l",ts,direction,decision,json.dumps(meta),f"k{i}"))
        for i,close in enumerate([100,99,98,97,96]):
            con.execute("INSERT INTO history_bars VALUES(?,?,?,?)",
                        ("XAUUSD_l","M15",ts+900*i,close))
        con.execute("INSERT INTO trade_outcomes VALUES(?,?,?,?,?,?,?,?,?,?)",
                    ("k2","trade-1","SELL",ts+15,ts+100,"XAUUSD_l","HUMAN_ASSISTED",26092212,"0.59.7",99.0))
    result=audit(p,"2026-10-12")
    assert result["saved_buy_sell_decisions"]==1
    assert result["signals"][0]["match_status"].startswith("RECORDED_TRADE")
    assert result["wait_opportunity_groups"]==1
    assert result["wait_groups"][0]["future_close_observation"]["favorable_direction_close_move"]==4
    assert result["wait_groups"][0]["also_had_final_trade_signal"] is True
    assert audit(p,"2026-10-11")["saved_buy_sell_decisions"]==0
