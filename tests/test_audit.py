from __future__ import annotations

import io
import json
import sqlite3

from ramon.audit import audit, csv_candidates
from ramon.history import ensure_history_db
from ramon.report import generate_report
from ramon.train_roles import load_trade_examples, train_bundle
from test_role_learning import seed_trade


def test_audit_matches_trainer_and_does_not_modify_database_or_checkpoints(tmp_path, capsys):
    db = ensure_history_db(tmp_path / 'history.db')
    for i in range(1, 5):
        seed_trade(db, i)
    with sqlite3.connect(db) as con:
        con.execute("UPDATE trade_outcomes SET training_status='CENSORED_MANUAL' WHERE sample_key=?", (f'{2:016x}',))
        con.execute("UPDATE decision_samples SET quote_time=quote_time-100 WHERE sample_key=?", (f'{3:016x}',))
        con.execute("UPDATE decision_samples SET chronos_model='another/model' WHERE sample_key=?", (f'{4:016x}',))
    assert len(load_trade_examples(db, 'XAUUSD_l', 'test/model')) == 1
    before = db.read_bytes()
    root = tmp_path / 'roles'
    audit(str(db), 'XAUUSD_l', 'test/model', root, trade_number=1)
    text = capsys.readouterr().out
    assert '"eligible_closed_trades": 1' in text
    assert '"LEARNABLE": 3' in text  # clean exit labels are not the training count
    assert '"remaining_to_sample_gate": 499' in text
    assert 'UNKNOWN_LEGACY' in text
    assert '"trade_key": "real-account:1"' in text
    assert '"entry_delay_broker_seconds": 1' in text
    assert db.read_bytes() == before
    assert not root.exists()


def test_audit_missing_legacy_schema_is_unknown_not_zero_and_keeps_trade_evidence(tmp_path, capsys):
    db = ensure_history_db(tmp_path / 'history.db')
    seed_trade(db, 1)
    with sqlite3.connect(db) as con:
        con.execute('DROP TABLE decision_samples')
    before = db.read_bytes()
    audit(str(db), 'XAUUSD_l', 'test/model', tmp_path / 'roles', trade_key='real-account:1')
    text = capsys.readouterr().out
    assert 'Trainer selection failed' in text
    assert 'eligible_closed_trades' not in text
    assert 'UNKNOWN_LEGACY' in text
    assert db.read_bytes() == before


def test_audit_preserves_recorded_entry_settings_and_reports_last_attempt_separately(tmp_path, capsys):
    db = ensure_history_db(tmp_path / 'history.db')
    seed_trade(db, 1)
    snapshot = {'base': {'reason': 'ai_trend_continuation_up', 'signal_strength': .0281},
                'final': {'decision': 'BUY', 'reason': 'ai_trend_continuation_up'},
                'settings': {'trend_min_edge_fraction': .42}}
    with sqlite3.connect(db) as con:
        con.execute('UPDATE decision_samples SET model_metadata=?', (json.dumps({'decision_audit': snapshot}),))
    root = tmp_path / 'roles'
    root.mkdir()
    (root / 'training_report.json').write_text(json.dumps({'status': 'waiting_for_closed_trades',
                                                          'closed_trade_samples': 0}))
    audit(str(db), 'XAUUSD_l', 'test/model', root, trade_key='real-account:1')
    text = capsys.readouterr().out
    assert '"trend_min_edge_fraction": 0.42' in text
    assert '"closed_trade_samples": 0' in text
    assert '"eligible_closed_trades": 1' in text
    assert 'UNKNOWN_LEGACY' not in text


def test_signal_csv_returns_all_time_candidates_without_claiming_an_exact_join():
    csv = io.StringIO('captured,symbol,decision,reason,min_lot_override_used\n'
                     '2026.09.23 18:14:00,XAUUSD_l,BUY,ai_trend_continuation_up,YES\n'
                     '2026.09.23 18:14:30,XAUUSD_l,WAIT,insufficient_model_edge,NO\n'
                     '2026.09.23 18:14:00,EURUSD_l,BUY,forecast_up,NO\n'
                     '2026.09.23 18:20:00,XAUUSD_l,BUY,forecast_up,NO\n')
    from datetime import datetime, timezone
    broker = int(datetime(2026, 9, 23, 18, 14, tzinfo=timezone.utc).timestamp())
    rows = csv_candidates(csv, {'symbol': 'XAUUSD_l', 'opened': broker + 2}, {'quote_time': broker})
    assert len(rows) == 2
    assert rows[0]['min_lot_override_used'] == 'YES'
    assert rows[1]['decision'] == 'WAIT'


def test_report_does_not_negate_already_aligned_sell_movement_or_trend_magnitude(tmp_path, capsys):
    db = ensure_history_db(tmp_path / 'history.db')
    for i in (1, 2):
        seed_trade(db, i)
    with sqlite3.connect(db) as con:
        con.execute("UPDATE trade_outcomes SET direction='SELL' WHERE sample_key=?", (f'{2:016x}',))
        con.execute("UPDATE decision_samples SET direction='SELL',final_decision='SELL' WHERE sample_key=?", (f'{2:016x}',))
        con.execute('UPDATE decision_samples SET entry_features=?',
                    (json.dumps({'intrabar_move_atr': .4, 'ai_trend_score': .6}),))
        con.execute('UPDATE decision_samples SET regime_features=? WHERE sample_key=?',
                    (json.dumps({'ret_1_atr': -.5}), f'{2:016x}'))
        con.execute('UPDATE decision_samples SET regime_features=? WHERE sample_key=?',
                    (json.dumps({'ret_1_atr': .5}), f'{1:016x}'))
    generate_report(str(db), LIMIT=0)
    text = capsys.readouterr().out
    for feature, value in [('entry.intrabar_move_atr.aligned', .4), ('entry.ai_trend_score', .6),
                           ('regime.ret_1_atr.aligned', .5)]:
        line = next(line for line in text.splitlines() if line.startswith(feature))
        assert f'WIN={value:9.4f} LOSS={value:9.4f}' in line
    assert 'entry.ai_trend_score.aligned' not in text


def test_waiting_training_report_records_limits_and_timestamp(tmp_path):
    db = ensure_history_db(tmp_path / 'history.db')
    seed_trade(db, 1)
    report = train_bundle(db=db, symbol='XAUUSD_l', chronos_model='test/model', out=tmp_path / 'roles')
    assert report['status'] == 'waiting_for_closed_trades'
    assert report['minimum_samples'] == 500
    assert report['remaining_to_sample_gate'] == 499
    assert report['symbol'] == 'XAUUSD_l'
    assert report['generated_at_utc'].endswith('+00:00')
