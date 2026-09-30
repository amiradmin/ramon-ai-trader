import json
import sqlite3

import pytest

from ramon.core import Forecast
from ramon.forward_research import (
    MODELS, begin_experiment, evaluate_pending, open_ledger, record_once, snapshot, summarize,
)
from ramon.history import ensure_history_db
from test_research_compare import model

ASOF = 1800000000
OFFSET = 10800
NOW = ASOF + 1080 - OFFSET


def setup(tmp_path):
    source = tmp_path / "history.sqlite3"
    ensure_history_db(source)
    with sqlite3.connect(source) as c:
        c.executemany("INSERT INTO history_bars VALUES ('XAUUSD_l','M15',?,?,?,?,?,20)",
                      [(ASOF-900*(255-i), 100+i*.1, 102+i*.1, 99+i*.1, 100+i*.1) for i in range(256)])
        c.execute("""INSERT INTO decision_samples
            (captured,symbol,signal_bar_time,mid,spread,atr,direction,base_decision,
             regime_features,entry_features,meta_base_features,quote_time,sample_key)
            VALUES (?,'XAUUSD_l',?,125.5,.2,2,'BUY','WAIT','{}','{}','{}',?,'abc')""",
                  (NOW-15, ASOF, NOW-20+OFFSET))
        c.execute("""INSERT INTO trade_outcomes
            (trade_key,sample_key,symbol,direction,opened,closed,net_units,initial_risk_units,
             net_r,exit_reason,received,opened_utc_offset_seconds,closed_utc_offset_seconds)
            VALUES ('a','a','XAUUSD_l','BUY',?,?,1,1,1,'DEAL_REASON_TP',?,10800,10800)""",
                  (ASOF-10000, ASOF-1000, NOW))
    ledger = open_ledger(tmp_path / "forward.sqlite3", source)
    forecaster = model()
    manifest = begin_experiment(ledger, forecaster, "XAUUSD_l", now=NOW-100)
    return source, ledger, forecaster, manifest


def future(source, count=4, gap=False):
    with sqlite3.connect(source) as c:
        c.executemany("INSERT INTO history_bars VALUES ('XAUUSD_l','M15',?,?,?,?,?,0)",
                      [(ASOF+900*(i+1+(1 if gap else 0)), 125.5+i*.5, 130, 120, 125.5+(i+1)*.5)
                       for i in range(count)])


def test_paired_record_immutable_context_deduplicated_and_live_db_unchanged(tmp_path):
    source, conn, forecaster, manifest = setup(tmp_path)
    before = source.read_bytes()
    assert record_once(source, conn, manifest, forecaster, clock=lambda: NOW)["status"] == "recorded"
    assert source.read_bytes() == before
    stored = conn.execute("SELECT payload FROM predictions").fetchone()[0]
    payload = json.loads(stored)
    assert set(payload["forecasts"]) == set(MODELS)
    assert len(payload["bars"]) == 256
    assert payload["bars"][-1]["time"] == ASOF
    assert forecaster.pipeline.tasks[0]["target"][-1] == 125.5
    assert record_once(source, conn, manifest, forecaster, clock=lambda: NOW)["status"] == "already_recorded"
    assert len(forecaster.pipeline.tasks) == 1
    future(source)
    assert conn.execute("SELECT payload FROM predictions").fetchone()[0] == stored


@pytest.mark.parametrize("change,expected", [
    ("UPDATE decision_samples SET captured=captured-3600", "waiting_for_fresh_sample"),
    ("UPDATE decision_samples SET quote_time=quote_time-3600", "waiting_for_fresh_quote"),
    ("UPDATE decision_samples SET quote_time=NULL", "waiting_for_live_sample"),
    ("UPDATE trade_outcomes SET opened_utc_offset_seconds=NULL,closed_utc_offset_seconds=NULL",
     "waiting_for_agreeing_recent_broker_offset"),
    ("UPDATE trade_outcomes SET opened_utc_offset_seconds=7200", "waiting_for_agreeing_recent_broker_offset"),
    ("UPDATE decision_samples SET signal_bar_time=signal_bar_time-900", "quote_bar_mismatch"),
    ("DELETE FROM history_bars WHERE time=1800000000", "waiting_for_matching_completed_context"),
    ("UPDATE decision_samples SET spread=0", "invalid_quote_or_atr"),
])
def test_stale_missing_or_inconsistent_source_is_not_backfilled(tmp_path, change, expected):
    source, conn, forecaster, manifest = setup(tmp_path)
    with sqlite3.connect(source) as c:
        c.execute(change)
    result = record_once(source, conn, manifest, forecaster, clock=lambda: NOW)
    assert result["status"] == expected
    assert not conn.execute("SELECT * FROM predictions").fetchall()
    assert not forecaster.pipeline.tasks


def test_experiment_cannot_mix_checkpoint_or_config_and_restarts_keep_boundary(tmp_path):
    _, conn, forecaster, manifest = setup(tmp_path)
    assert begin_experiment(conn, forecaster, "XAUUSD_l", now=NOW+1000) == manifest
    with pytest.raises(ValueError, match="changed"):
        begin_experiment(conn, forecaster, "XAUUSD_l", context=128)
    forecaster.revision = "new-weights"
    with pytest.raises(ValueError, match="changed"):
        begin_experiment(conn, forecaster, "XAUUSD_l")
    forecaster.revision = None
    with pytest.raises(ValueError, match="identifiable"):
        begin_experiment(conn, forecaster, "XAUUSD_l")


def test_refuses_live_database_as_ledger_including_hardlink(tmp_path):
    source, _, _, _ = setup(tmp_path)
    before = source.read_bytes()
    with pytest.raises(ValueError, match="separate"):
        open_ledger(source, source)
    import os
    link = tmp_path / "alias.sqlite3"
    os.link(source, link)
    with pytest.raises(ValueError, match="separate"):
        open_ledger(link, source)
    assert source.read_bytes() == before


def test_all_models_must_finish_before_any_target_is_closed(tmp_path):
    source, conn, forecaster, manifest = setup(tmp_path)
    timestamps = iter([NOW, ASOF+1800-OFFSET])
    result = record_once(source, conn, manifest, forecaster, clock=lambda: next(timestamps))
    assert result["status"] == "excluded_late_forecast"
    assert not conn.execute("SELECT * FROM predictions").fetchall()


def test_failed_or_invalid_forecast_never_saves_partial_pair(tmp_path):
    source, conn, forecaster, manifest = setup(tmp_path)
    forecaster.forecast = lambda closes, horizon: Forecast(1, float("nan"), 3, (1, 1, 1, 1))
    with pytest.raises(ValueError, match="invalid paired"):
        record_once(source, conn, manifest, forecaster, clock=lambda: NOW)
    assert not conn.execute("SELECT * FROM predictions").fetchall()


def test_labels_wait_for_full_closed_horizon_and_are_frozen(tmp_path):
    source, conn, forecaster, manifest = setup(tmp_path)
    record_once(source, conn, manifest, forecaster, clock=lambda: NOW)
    future(source, count=3)
    assert evaluate_pending(source, conn, manifest, now=NOW+9000)["new_matured"] == 0
    with sqlite3.connect(source) as c:
        c.execute("INSERT INTO history_bars VALUES ('XAUUSD_l','M15',?,127,130,120,127.5,0)", (ASOF+3600,))
    assert evaluate_pending(source, conn, manifest, now=ASOF+4500-OFFSET-1)["new_matured"] == 0
    assert evaluate_pending(source, conn, manifest, now=ASOF+4500-OFFSET)["new_matured"] == 1
    before = summarize(conn)
    with sqlite3.connect(source) as c:
        c.execute("UPDATE history_bars SET high=999,close=999 WHERE time>?", (ASOF,))
    assert evaluate_pending(source, conn, manifest, now=NOW+9000)["new_matured"] == 0
    assert summarize(conn) == before


def test_same_targets_scored_for_all_models_and_metrics_are_forecasts_not_pnl(tmp_path):
    source, conn, forecaster, manifest = setup(tmp_path)
    record_once(source, conn, manifest, forecaster, clock=lambda: NOW)
    future(source)
    evaluate_pending(source, conn, manifest, now=NOW+9000)
    report = summarize(conn)
    assert report["recorded_pairs"] == report["paired_matured_samples"] == 1
    assert report["pending_pairs"] == 0
    assert report["no_change_path_mae_price"] == 1.25
    enriched = report["results"]["chronos_past_covariates"]
    assert enriched["path_mae_price"] == 0
    assert enriched["path_mae_relative_to_no_change"] == 0
    assert enriched["direction_accuracy"] == 1
    assert enriched["endpoint_interval_80_coverage"] == 1
    assert enriched["endpoint_pinball_price"] == pytest.approx((.1*4+.1*1)/3)
    assert all(r["paired_samples"] == 1 for r in report["results"].values())
    assert report["promotion_allowed"] is False
    assert report["selected_model"] is None
    assert "net_r" not in enriched


def test_gaps_are_reported_and_excluded_for_every_model(tmp_path):
    source, conn, forecaster, manifest = setup(tmp_path)
    record_once(source, conn, manifest, forecaster, clock=lambda: NOW)
    future(source, gap=True)
    result = evaluate_pending(source, conn, manifest, now=NOW+9000)
    assert result == {"new_matured": 0, "new_excluded": 1}
    report = summarize(conn)
    assert report["outcome_status_counts"] == {"excluded_gap": 1}
    assert all(r["paired_samples"] == 0 and r["path_mae_price"] is None for r in report["results"].values())


def test_imported_future_bars_cannot_enter_live_input_snapshot(tmp_path):
    source, conn, forecaster, manifest = setup(tmp_path)
    future(source)
    snap, status = snapshot(source, manifest, NOW)
    assert snap is None
    assert status == "waiting_for_matching_completed_context"
    assert record_once(source, conn, manifest, forecaster, clock=lambda: NOW)["status"] == status
