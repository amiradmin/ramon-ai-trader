import json
import sqlite3

from ramon.monitor import persist_human_assisted_event


def test_human_assisted_event_persists_v2_v3_and_timing(tmp_path):
    db=tmp_path/"history.db"
    row={
        "sample_key":"abcdef1234567890",
        "signal_bar_time":1800000000,
        "v2_decision":"SELL",
        "v2_reason":"ai_engine_v2_sell",
        "v3_shadow_decision":"WAIT",
        "v3_shadow_reason":"entry_timing_not_ready",
        "v3_shadow_block":"entry_timing_not_ready",
        "buy_success_probability":0.41,
        "sell_success_probability":0.72,
        "entry_timing_ready":0,
        "forecast_distance_atr":0.91,
        "market_state":"TREND_DOWN",
        "moment_label":"NORMAL",
        "market_direction":"SELL",
        "entry_probability":0.58,
        "full_sl_probability":0.31,
        "ai_score":0.63,
    }
    persist_human_assisted_event(
        db,"XAUUSD_l",row,action="ENTER",direction="SELL",mode="DISCRETIONARY"
    )
    with sqlite3.connect(db) as con:
        saved=con.execute(
            """SELECT action,human_direction,entry_mode,v2_decision,v3_shadow_decision,
                      entry_timing_ready,forecast_distance_atr,snapshot_json
               FROM human_assisted_events"""
        ).fetchone()
    assert saved[:7] == ("ENTER","SELL","DISCRETIONARY","SELL","WAIT",0,0.91)
    snap=json.loads(saved[7])
    assert snap["v3_shadow_block"] == "entry_timing_not_ready"
    assert snap["sell_quality"] == 0.72
