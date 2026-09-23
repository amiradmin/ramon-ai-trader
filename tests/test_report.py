"""Report regression coverage for real cost reconciliation and mixed telemetry vintages."""
from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path

import pytest

from ramon.history import ensure_history_db, persist_trade_outcome
from ramon.report import generate_report, trade_time


def outcome(**changes):
    return {"trade_key": "server:1:2", "sample_key": "a" * 16, "symbol": "XAUUSD_l",
            "direction": "BUY", "opened": 1_800_010_800, "closed": 1_800_014_400,
            "net_units": 7.5, "initial_risk_units": 6, "exit_reason": "DEAL_REASON_EXPERT", **changes}


def telemetry(**changes):
    return {"profit_units": 10, "commission_units": -2, "swap_units": -.4, "fee_units": -.1,
            "opened_utc_offset_seconds": 10800, "closed_utc_offset_seconds": 10800,
            "exit_detail": "maximum_hold_bars", "entry_ea_version": "0.28", **changes}


def test_legacy_migration_enrichment_and_duplicate_delivery(tmp_path):
    db = tmp_path / "history.sqlite3"
    with sqlite3.connect(db) as con:
        con.execute("""CREATE TABLE trade_outcomes (
            trade_key TEXT PRIMARY KEY, sample_key TEXT NOT NULL UNIQUE, symbol TEXT NOT NULL,
            direction TEXT NOT NULL, opened INTEGER NOT NULL, closed INTEGER NOT NULL,
            net_units REAL NOT NULL, initial_risk_units REAL NOT NULL, net_r REAL NOT NULL,
            exit_reason TEXT NOT NULL, received INTEGER NOT NULL)""")
    persist_trade_outcome(db, outcome(), 1_800_014_500)
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT profit_units,exit_detail FROM trade_outcomes").fetchone() == (None, None)
    persist_trade_outcome(db, outcome(**telemetry()), 1_800_014_600)
    persist_trade_outcome(db, outcome(), 1_800_014_700)
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT COUNT(*),net_units,net_r,profit_units,commission_units,swap_units,fee_units,exit_detail FROM trade_outcomes").fetchone() == (1, 7.5, 1.25, 10, -2, -.4, -.1, "maximum_hold_bars")


@pytest.mark.parametrize("extra", [
    {"profit_units": 10}, telemetry(profit_units=11), telemetry(fee_units=float('nan')),
    telemetry(opened_utc_offset_seconds=90000), telemetry(closed_utc_offset_seconds=1.5),
])
def test_invalid_telemetry_cannot_overwrite_outcome(tmp_path, extra):
    db = tmp_path / "history.sqlite3"
    persist_trade_outcome(db, outcome(), 123)
    with pytest.raises(ValueError):
        persist_trade_outcome(db, outcome(**extra), 124)
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT received,profit_units FROM trade_outcomes").fetchone() == (123, None)


def test_report_legacy_database_is_read_only_and_does_not_guess(tmp_path, capsys):
    db = tmp_path / "legacy.sqlite3"
    persist_trade_outcome(db, outcome(), 123)
    # Recreate precisely the previous trade schema, without any new columns.
    with sqlite3.connect(db) as con:
        con.execute("ALTER TABLE trade_outcomes RENAME TO old")
        con.execute("""CREATE TABLE trade_outcomes AS SELECT trade_key,sample_key,symbol,direction,
                    opened,closed,net_units,initial_risk_units,net_r,exit_reason,received FROM old""")
        con.execute("DROP TABLE old")
        con.execute("ALTER TABLE decision_samples DROP COLUMN model_metadata")
    original = db.read_bytes()
    generate_report(str(db), LIMIT=0)
    text = capsys.readouterr().out
    assert "Complete breakdown: 0/1" in text
    assert "BROKER[UTC offset unknown]" in text
    assert "UNKNOWN expert trigger" in text
    assert "model=UNKNOWN" in text
    assert "Generated:" in text and " UTC" in text
    assert "END RAMON REPORT" in text
    assert db.read_bytes() == original


def test_report_links_entry_provenance_and_preserves_net(tmp_path, capsys):
    db = tmp_path / "history.sqlite3"
    persist_trade_outcome(db, outcome(**telemetry()), 123)
    with sqlite3.connect(db) as con:
        con.execute("""INSERT INTO decision_samples
            (captured,symbol,signal_bar_time,mid,spread,atr,direction,base_decision,
             regime_features,entry_features,meta_base_features,sample_key,chronos_model,bundle_id,model_metadata)
            VALUES (1,'XAUUSD_l',1,100,.4,2,'BUY','BUY','{}','{}','{}',?,?,?,?)""",
            ('a' * 16, 'checkpoint-at-entry', 'bundle-at-entry', json.dumps({
                'chronos_revision': 'sha256:abc', 'ensemble_active': 1,
                'role_manifest': {'created_at_utc': '2026-09-23T00:00:00+00:00'}})))
    generate_report(str(db), LIMIT=0)
    text = capsys.readouterr().out
    assert "Net account units   : +7.5000" in text
    assert "Complete breakdown: 1/1" in text
    assert "Reconciliation delta: +0.00000000" in text
    assert "model=checkpoint-at-entry" in text
    assert "bundle=bundle-at-entry" in text
    assert "revision=sha256:abc" in text
    assert "EA=0.28" in text
    assert "maximum_hold_bars" in text
    assert "bundle_created_utc=2026-09-23T00:00:00+00:00" in text
    assert "Trades with entry and exit UTC offsets: 1/1" in text


def test_missing_database_is_not_created(tmp_path):
    db = tmp_path / 'missing.sqlite3'
    with pytest.raises(sqlite3.OperationalError):
        generate_report(str(db))
    assert not db.exists()


def test_times_do_not_depend_on_host_timezone_and_keep_event_offsets(monkeypatch):
    row = outcome(**telemetry(closed_utc_offset_seconds=7200))
    original = os.environ.get('TZ')
    try:
        monkeypatch.setenv('TZ', 'UTC')
        time.tzset()
        expected = (trade_time(row, 'opened'), trade_time(row, 'closed'))
        monkeypatch.setenv('TZ', 'Asia/Tehran')
        time.tzset()
        assert (trade_time(row, 'opened'), trade_time(row, 'closed')) == expected
        assert expected[0].endswith('UTC')
        assert trade_time(outcome(), 'opened').endswith('BROKER[UTC offset unknown]')
    finally:
        if original is None:
            os.environ.pop('TZ', None)
        else:
            os.environ['TZ'] = original
        time.tzset()


def test_empty_database_report(tmp_path, capsys):
    db = ensure_history_db(tmp_path / 'empty.sqlite3')
    generate_report(str(db))
    assert 'No closed Ramon trades found.' in capsys.readouterr().out


def test_legacy_correction_invalidates_stale_costs_and_exit_metadata(tmp_path):
    db = tmp_path / 'history.sqlite3'
    persist_trade_outcome(db, outcome(**telemetry()), 123)
    persist_trade_outcome(db, outcome(net_units=7, closed=1_800_015_000, exit_reason='DEAL_REASON_SL'), 124)
    with sqlite3.connect(db) as con:
        assert con.execute('SELECT net_units,profit_units,exit_detail,closed_utc_offset_seconds,entry_ea_version FROM trade_outcomes').fetchone() == (7, None, None, None, '0.28')


def test_report_without_decision_table_still_reports_unjoined_trades(tmp_path, capsys):
    db = tmp_path / 'history.sqlite3'
    persist_trade_outcome(db, outcome(), 123)
    with sqlite3.connect(db) as con:
        con.execute('DROP TABLE decision_samples')
    generate_report(str(db))
    output = capsys.readouterr().out
    assert 'model=UNKNOWN' in output
    assert 'Joined samples: 0' in output
    assert 'END RAMON REPORT' in output


def test_checkpoint_revision_detects_replaced_weights_at_same_path(tmp_path):
    from ramon.model import checkpoint_revision
    checkpoint = tmp_path / 'model'
    checkpoint.mkdir()
    (checkpoint / 'config.json').write_text('{}')
    weights = checkpoint / 'model.safetensors'
    weights.write_bytes(b'first weights')
    first = checkpoint_revision(str(checkpoint), object())
    assert first == checkpoint_revision(str(checkpoint), object())
    weights.write_bytes(b'new weights')
    assert first != checkpoint_revision(str(checkpoint), object())
    assert checkpoint_revision('autogluon/chronos-2-small', object()) is None
