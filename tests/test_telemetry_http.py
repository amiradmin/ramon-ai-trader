from dataclasses import asdict
import json
import sqlite3
from urllib.error import HTTPError

import pytest

from test_learning_api import learning_server
from test_decision import bars
from ramon.telemetry import replay_input, persist_events, observed_path_labels, export_dataset, backup_database
from test_telemetry import sample, outcome, event
from ramon.history import persist_trade_outcome


def test_real_http_records_exact_request_all_response_and_concurrent_samples(learning_server):
    db, post=learning_server
    candles=bars()
    request=dict(symbol="XAUUSD_l",timeframe="M15",bid=100,ask=100.4,point=.01,
                 bars=[{**asdict(b),"tick_volume":27,"spread_points":42} for b in candles],
                 quote_time=candles[-1].time+905, ea_context={"role":"MAIN"})
    first=post("/decision",request)
    second=post("/decision",{**request,"ea_context":{"role":"SMALL"}})
    assert first["telemetry_saved"]==second["telemetry_saved"]==1
    assert first["sample_key"]!=second["sample_key"]
    with sqlite3.connect(db) as con:
        assert replay_input(con,first["sample_key"])==request
        assert con.execute("SELECT COUNT(*) FROM inference_audit").fetchone()[0]==2
        provenance, response = con.execute("SELECT provenance_json,response_json FROM inference_audit WHERE sample_key=?", (second["sample_key"],)).fetchone()
        info=json.loads(provenance)
        assert info["forecast_cache_hit"] is True
        assert len(info["implementation_sha256"])==64
        assert json.loads(response)==second


def test_real_http_event_ack_retry_and_manual_outcome(learning_server):
    db, post=learning_server
    data=event()
    assert post("/events",{"events":[data]})=={"saved":True,"acknowledged":["evt-1"]}
    assert post("/events",{"events":[data]})=={"saved":True,"acknowledged":["evt-1"]}
    assert post("/trades",outcome(reason="DEAL_REASON_CLIENT"))=={"saved":True}
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT COUNT(*) FROM research_events").fetchone()[0]==1
        assert con.execute("SELECT training_status FROM trade_outcomes").fetchone()[0]=="CENSORED_MANUAL"
    with pytest.raises(HTTPError) as error:
        post("/events",{"events":[{**data,"data":{"reason":"different"}}]})
    assert error.value.code==400


def test_audit_failure_does_not_change_live_decision_or_hide_failure(learning_server,monkeypatch):
    db, post=learning_server
    import ramon.server as service
    def fail(*args,**kwargs):
        raise OSError("disk failure")
    monkeypatch.setattr(service,"persist_inference",fail)
    result=post("/decision",dict(symbol="XAUUSD_l",timeframe="M15",bid=100,ask=100.4,
        point=.01,bars=[asdict(b) for b in bars()]))
    assert result["decision"]=="BUY"
    assert result["sample_saved"]==1
    assert result["telemetry_saved"]==0


def test_sampled_path_uses_executable_side_and_does_not_claim_tick_precision(tmp_path):
    db=tmp_path/"db"
    from ramon.telemetry import persist_inference
    sample(db)
    persist_inference(db,sample_key="a"*16,request={"bars":[]},
        response={"target_direction":"SELL","target_tp1":99},provenance={},recorded_utc=1)
    rows=[]
    for number,(bid,ask) in enumerate(((98.5,99.2),(98,98.4),(100.2,100.6))):
        record=event(str(number))
        record["kind"]="position_quote"
        record["broker_time_msc"]+=number*1000
        record["data"]={"bid":bid,"ask":ask,"entry_price":100,"position_type":1,"sampling_interval_ms":1000}
        rows.append(record)
    persist_events(db,{"events":rows},1)
    with sqlite3.connect(db) as con:
        label=observed_path_labels(con,"a"*16)
    assert label["mfe_price_observed"]==pytest.approx(1.6)
    assert label["mae_price_observed"]==pytest.approx(.6)
    assert label["target_hits"]["tp1_first_observed_msc"]==2001000
    assert label["exact_first_touch_order_available"] is False


def test_export_preserves_outcomes_with_missing_input_and_unlinked_events(tmp_path):
    db=tmp_path/"db"
    persist_trade_outcome(db,outcome(reason="DEAL_REASON_WEB"),1)
    unlinked=event();unlinked["sample_key"]=""
    persist_events(db,{"events":[unlinked]},1)
    out=tmp_path/"all.jsonl"
    export_dataset(db,out)
    records=[json.loads(line) for line in out.read_text().splitlines()]
    assert [r["record_type"] for r in records]==["orphan_trade","unlinked_event"]
    assert records[0]["trade"]["net_units"]==5


def test_backup_includes_committed_wal_without_migrating_source(tmp_path):
    db=tmp_path/"db"; out=tmp_path/"backup"
    with sqlite3.connect(db) as con:
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("CREATE TABLE sentinel (value)")
        con.execute("INSERT INTO sentinel VALUES ('before')")
        con.commit()
        backup_database(db,out)
        with sqlite3.connect(out) as copied:
            assert copied.execute("SELECT value FROM sentinel").fetchone()[0]=="before"
        assert con.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='decision_samples'").fetchone()[0]==0
    with pytest.raises(ValueError):
        backup_database(db,out)



def test_spread_veto_cannot_attribute_previous_cached_forecast(learning_server):
    db,post=learning_server
    request=dict(symbol='XAUUSD_l',timeframe='M15',bid=100,ask=100.4,point=.01,
                 bars=[asdict(b) for b in bars()])
    post('/decision',request)
    veto=post('/decision',{**request,'ask':102})
    assert veto['reason']=='spread_or_atr'
    with sqlite3.connect(db) as con:
        raw=con.execute('SELECT provenance_json FROM inference_audit WHERE sample_key=?',(veto['sample_key'],)).fetchone()[0]
    audit=json.loads(raw)
    assert audit['forecast'] is None
    assert audit['forecast_cache_hit'] is None
    assert audit['forecast_context_end_mt5'] is None
