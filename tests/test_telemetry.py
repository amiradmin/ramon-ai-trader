from dataclasses import asdict
import json
import sqlite3
import hashlib
import zlib

import pytest

from ramon.core import Market
from ramon.history import ensure_history_db, persist_decision_sample, persist_market, persist_trade_outcome
from ramon.telemetry import (persist_inference, persist_events, replay_input,
                             quality_report, export_dataset, backfill_opportunities)
from test_decision import bars


def sample(db, key="a"*16, captured=1000):
    market = Market("XAUUSD_l", "M15", 100, 100.4, .01, bars())
    assert persist_decision_sample(db, captured=captured, market=market,
        signal_bar_time=market.bars[-1].time, atr=1, direction="BUY",
        base_decision="BUY", final_decision="BUY",
        regime_features={}, entry_features={}, meta_base_features={},
        sample_key=key, chronos_model="test/fake",
        quote_time=market.bars[-1].time+905, news_features={})
    return market


def outcome(key="a"*16, reason="DEAL_REASON_TP", **extra):
    return dict(trade_key="server:123:987", sample_key=key, symbol="XAUUSD_l",
        direction="BUY", opened=1800, closed=2000, net_units=5, initial_risk_units=6,
        exit_reason=reason, trade_role="MAIN", entry_ea_version="0.53.6",
        profit_units=6, commission_units=-1, swap_units=0, fee_units=0, **extra)


def event(identity="evt-1", **extra):
    return dict(event_id=identity, account_key="server:123", role="MAIN",
        symbol="XAUUSD_l", kind="deal", sample_key="a"*16,
        position_id="987", broker_time_msc=2000000,
        data={"reason": "DEAL_REASON_CLIENT", "fee_units": -.1}, **extra)


def test_same_second_main_and_small_survive_and_keys_deduplicate(tmp_path):
    db = tmp_path/"db"
    sample(db)
    sample(db, "b"*16)
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT COUNT(*) FROM decision_samples").fetchone()[0] == 2


def test_old_unique_schema_migrates_without_losing_ids_columns_or_targets(tmp_path):
    db=tmp_path/"db"
    from ramon.history import DECISION_SAMPLE_SCHEMA
    with sqlite3.connect(db) as con:
        con.execute(DECISION_SAMPLE_SCHEMA.replace("meta_base_features TEXT NOT NULL",
                    "meta_base_features TEXT NOT NULL, UNIQUE(captured, symbol)"))
        con.execute("ALTER TABLE decision_samples ADD COLUMN extra_legacy TEXT")
        con.execute("INSERT INTO decision_samples VALUES (7,1,'XAUUSD_l',1,100,.4,1,'BUY','BUY','{}','{}','{}','keep')")
    ensure_history_db(db)
    # This also verifies the migration is idempotent.
    ensure_history_db(db)
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT id,extra_legacy FROM decision_samples").fetchone()==(7,"keep")
        assert "UNIQUE(captured, symbol)" not in con.execute("SELECT sql FROM sqlite_master WHERE name='decision_samples'").fetchone()[0]
    sample(db)
    sample(db, "b"*16)


def test_exact_context_replay_and_dedup_with_raw_volume_and_micro_bars(tmp_path):
    db=tmp_path/"db"
    market=sample(db)
    request={**asdict(market), "bars":[{**asdict(b),"tick_volume":123} for b in market.bars],
             "quote_time_msc":1234567890,"ea_context":{"role":"MAIN"}}
    for key in ("a"*16,"b"*16):
        persist_inference(db,sample_key=key,request=request,response={"decision":"BUY"},
                          provenance={"duration_ms":12},recorded_utc=100)
    with sqlite3.connect(db) as con:
        assert replay_input(con,"a"*16)==json.loads(json.dumps(request))
        assert con.execute("SELECT COUNT(*) FROM input_blobs").fetchone()[0]==2
        con.execute("UPDATE input_blobs SET body=?", (zlib.compress(b"tampered"),))
        with pytest.raises(ValueError,match="hash mismatch"):
            replay_input(con,"a"*16)


def test_conflicting_inference_reuse_cannot_overwrite(tmp_path):
    db=tmp_path/"db"
    kwargs=dict(sample_key="a"*16, request={"bars":[]}, response={"decision":"WAIT"},
                provenance={}, recorded_utc=1)
    persist_inference(db,**kwargs)
    persist_inference(db,**kwargs)
    with pytest.raises(ValueError,match="conflicting"):
        persist_inference(db,**{**kwargs,"response":{"decision":"BUY"}})


def test_event_retry_is_idempotent_and_batch_conflict_rolls_back(tmp_path):
    db=tmp_path/"db"
    assert persist_events(db, {"events":[event()]}, 1)==["evt-1"]
    assert persist_events(db, {"events":[event()]}, 2)==["evt-1"]
    conflict=event();conflict["data"]["fee_units"]=-2
    with pytest.raises(ValueError,match="conflicting"):
        persist_events(db,{"events":[event("new"),conflict]},3)
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT COUNT(*) FROM research_events").fetchone()[0]==1


@pytest.mark.parametrize("reason,detail", [
    ("DEAL_REASON_CLIENT",None),("DEAL_REASON_MOBILE",None),("DEAL_REASON_WEB",None),
    ("DEAL_REASON_EXPERT","manual_dashboard_close")])
def test_all_manual_close_sources_stored_and_censored(tmp_path,reason,detail):
    db=tmp_path/"db"
    payload=outcome(reason=reason, **({"exit_detail":detail} if detail else {}))
    persist_trade_outcome(db,payload,1)
    persist_trade_outcome(db,payload,2)
    report=quality_report(db)
    assert report["closed_trades"]==1
    assert report["manual_closed_trades"]==1
    assert report["groups"][0]["net_units"]==5


def test_partial_manual_exit_then_tp_preserves_intervention_on_legacy_retry(tmp_path):
    db=tmp_path/"db"
    persist_trade_outcome(db,outcome(manual_intervention=1),1)
    persist_trade_outcome(db,outcome(),2)
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT training_status,manual_intervention FROM trade_outcomes").fetchone()==("CENSORED_MANUAL",1)


def test_legacy_manual_dashboard_status_is_backfilled(tmp_path):
    db=tmp_path/"db"
    persist_trade_outcome(db,outcome(reason="DEAL_REASON_EXPERT",exit_detail="manual_dashboard_close"),1)
    with sqlite3.connect(db) as con:
        con.execute("UPDATE trade_outcomes SET training_status='LEARNABLE'")
    ensure_history_db(db)
    assert quality_report(db)["manual_closed_trades"]==1


def test_unknown_legacy_intervention_stays_null(tmp_path):
    db=tmp_path/"db"
    persist_trade_outcome(db,outcome(),1)
    persist_trade_outcome(db,outcome(),2)
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT manual_intervention FROM trade_outcomes").fetchone()[0] is None


def test_export_keeps_manual_labels_and_exact_input_and_reports_missing_legacy(tmp_path):
    db=tmp_path/"db"
    market=sample(db)
    request=json.loads(json.dumps(asdict(market)))
    persist_inference(db,sample_key="a"*16,request=request,response={"decision":"BUY"},
                      provenance={},recorded_utc=1)
    persist_trade_outcome(db,outcome(reason="DEAL_REASON_CLIENT"),2)
    out=tmp_path/"dataset.jsonl"
    manifest=export_dataset(db,out)
    record=json.loads(out.read_text())
    assert record["input"]==request
    assert record["trade"][0]["training_status"]=="CENSORED_MANUAL"
    assert manifest["sha256"]==hashlib.sha256(out.read_bytes()).hexdigest()
    assert quality_report(db)["closed_missing_immutable_input"]==0


def test_opportunity_labels_require_contiguous_fully_future_bars_and_never_call_it_pnl(tmp_path):
    db=tmp_path/"db"
    market=sample(db)
    quote=market.bars[-1].time+905
    # Test market helper bars are spaced 900 seconds and aligned.
    first=(quote//900+1)*900
    persist_inference(db,sample_key="a"*16,request=asdict(market),
                      response={},provenance={},recorded_utc=1)
    with sqlite3.connect(db) as con:
        for i in (0,1,3):
            con.execute("INSERT INTO history_bars VALUES ('XAUUSD_l','M15',?,100,110,99,105,42)",(first+i*900,))
    assert backfill_opportunities(db)==0
    with sqlite3.connect(db) as con:
        con.execute("INSERT INTO history_bars VALUES ('XAUUSD_l','M15',?,100,110,99,105,42)",(first+1800,))
    assert backfill_opportunities(db)==1
    assert backfill_opportunities(db)==0
    with sqlite3.connect(db) as con:
        row=con.execute("SELECT endpoint_time,source FROM opportunity_labels").fetchone()
        assert row==(first+3600,"completed_m15_price_only_not_trade_pnl")


@pytest.mark.parametrize("update", [{"broker_time_msc":float("nan")},{"role":"OTHER"},
    {"sample_key":"garbage"},{"kind":"unknown"},{"symbol":"USDJPY"}])
def test_bad_events_are_not_acknowledged(tmp_path,update):
    bad={**event(),**update}
    with pytest.raises(ValueError):
        persist_events(tmp_path/"db",{"events":[bad]},1)



@pytest.mark.parametrize("event_first", [True, False])
def test_external_stop_change_censors_outcome_in_either_arrival_order(tmp_path, event_first):
    db=tmp_path/"db"
    change=event()
    change.update(kind="protection_change", data=dict(actor="external_unattributed",
        previous_known=True, sl_before=0, sl_after=100, tp_before=0, tp_after=0))
    if event_first:
        persist_events(db,{"events":[change]},1)
        persist_trade_outcome(db,outcome(),received=2)
    else:
        persist_trade_outcome(db,outcome(),received=2)
        persist_events(db,{"events":[change]},1)
    persist_trade_outcome(db,outcome(),received=3)  # retry must not remove censor
    assert quality_report(db)["closed_with_external_sl_tp"]==1
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT training_status FROM trade_outcomes").fetchone()[0]=="CENSORED_EXTERNAL_SL_TP"
    persist_trade_outcome(db,outcome(reason="DEAL_REASON_CLIENT"),received=4)
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT training_status FROM trade_outcomes").fetchone()[0]=="CENSORED_MANUAL"


def test_initial_stop_baseline_is_not_a_claim_of_manual_change(tmp_path):
    db=tmp_path/"db"
    initial=event();initial.update(kind="protection_baseline",data=dict(sl_before=None,sl_after=100))
    persist_events(db,{"events":[initial]},1)
    persist_trade_outcome(db,outcome(),received=2)
    assert quality_report(db)["closed_with_external_sl_tp"]==0
