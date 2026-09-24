from __future__ import annotations

import io
import json
import sqlite3
import re
from pathlib import Path

from ramon.audit import audit, csv_candidates, SIGNAL_COLUMNS_028, SIGNAL_COLUMNS_030
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


def test_audit_reports_exact_persisted_sizing_when_available(tmp_path, capsys):
    db = ensure_history_db(tmp_path / 'history.db')
    seed_trade(db, 1)
    with sqlite3.connect(db) as con:
        con.execute(
            """UPDATE trade_outcomes
               SET risk_budget_units=6.0, planned_volume=0.01, min_lot_sl_units=17.55,
                   min_lot_override_used=1, max_executable_risk_usd=0.20,
                   money_units_per_usd=100.0
               WHERE trade_key='real-account:1'"""
        )
    audit(str(db), 'XAUUSD_l', 'test/model', tmp_path / 'roles', trade_key='real-account:1')
    text = capsys.readouterr().out
    assert '=== STORED ENTRY SIZING ===' in text
    assert '"status": "EXACT"' in text
    assert '"min_lot_override_used": 1' in text
    assert '"risk_budget_units": 6.0' in text


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


# Regression: the header emitted by early EA versions remains at the top of a
# file when a newer EA appends 62-column rows. DictReader silently shifts fields.
OLD_SIGNAL_HEADER = (
    'captured,signal_bar_time,symbol,decision,reason,signal_bid,signal_ask,spread_points,'
    'forecast_low,forecast_median,forecast_high,atr,buy_edge,sell_edge,minimum_edge,'
    'uncertainty,signal_strength,minimum_strength,stop_distance,target_distance,'
    'live_armed,account_lock,account_currency,balance_units,risk_usd,money_units_per_usd,'
    'risk_budget_units,sizing_side,planned_volume,estimated_sl_units,min_lot_sl_units'
)
NEW_SIGNAL_ROW = (
    '2026.09.23 18:14:41,2026.09.23 18:00,XAUUSD_l,BUY,ai_trend_continuation_up,'
    'BUY,ai_trend_continuation_up,NO,NO,-1.000000,-1.000000,-1.000000,'
    '4295.07,4295.49,42,4279.63,4296.52,4316.49,11.7021,1.03,-1.87,1.40,36.86,'
    '0.028081,0.200000,NO,BUY,0.036745,0.128182,0.050000,0.030000,0.060000,'
    'YES,BUY,0.148364,0.197819,0.750000,0.150000,0.750000,0.250000,0.030000,'
    '17.55,35.11,YES,YES,REAL,CENT,1000.00,10.0000,0.0600,100.0000,6.0000,'
    'YES,0.2000,YES,BUY,0.0100,17.5500,0.1755,17.5500,0.1755,YES'
)


def mixed_csv_candidates(header, *rows):
    from datetime import datetime, timezone
    quote = int(datetime(2026, 9, 23, 18, 14, 40, tzinfo=timezone.utc).timestamp())
    return csv_candidates(io.StringIO('\ufeff' + header + '\n' + '\n'.join(rows)),
                          {'symbol': 'XAUUSD_l', 'opened': quote + 1}, {'quote_time': quote})


def test_old_header_new_row_recovers_known_layout_without_exposing_shifted_prices():
    row, = mixed_csv_candidates(OLD_SIGNAL_HEADER, NEW_SIGNAL_ROW)
    assert row['csv_schema_status'] == 'RECONSTRUCTED_KNOWN_LAYOUT'
    assert row['csv_header_columns'] == 31
    assert row['csv_row_columns'] == 62
    assert row['signal_strength'] == '0.028081'
    assert row['minimum_strength'] == '0.200000'
    assert row['base_reason'] == 'ai_trend_continuation_up'
    assert row['risk_usd'] == '0.0600'
    assert row['min_lot_override_used'] == 'YES'
    assert row['planned_volume'] == '0.0100'
    assert row['estimated_sl_units'] == '17.5500'
    assert row['seconds_from_stored_quote'] == 1


def test_unknown_width_or_invalid_field_types_suppress_numeric_evidence():
    truncated = NEW_SIGNAL_ROW.rsplit(',', 1)[0]
    invalid = NEW_SIGNAL_ROW.replace(',NO,NO,', ',4295.49,NO,', 1)
    for malformed in (truncated, invalid):
        row, = mixed_csv_candidates(OLD_SIGNAL_HEADER, malformed)
        assert row['csv_schema_status'] == 'UNREADABLE_LAYOUT'
        assert row['reason'] == 'ai_trend_continuation_up'
        assert row['signal_strength'] == 'UNKNOWN'
        assert row['planned_volume'] == 'UNKNOWN'
        assert row['csv_raw_fields']


def test_matching_header_and_repeated_new_header_are_not_treated_as_mismatched():
    header = ','.join(SIGNAL_COLUMNS_028)
    rows = mixed_csv_candidates(OLD_SIGNAL_HEADER, NEW_SIGNAL_ROW, header, NEW_SIGNAL_ROW)
    assert [r['csv_schema_status'] for r in rows] == ['RECONSTRUCTED_KNOWN_LAYOUT', 'HEADER_MATCH']
    assert rows[0]['signal_strength'] == rows[1]['signal_strength']


def test_reconstructed_layout_matches_the_actual_ea_writer():
    assert len(SIGNAL_COLUMNS_030) == 63  # MQL5 FileWrite supports at most 63 values.
    source = (Path(__file__).parents[1] / 'mt5' / 'Ramon.mq5').read_text()
    block = re.search(r'void AppendSignalCsv\(\).*?if\(empty\)\s*\{\s*FileWrite\(handle,(.*?)\);', source, re.S)
    assert block is not None
    assert tuple(re.findall(r'"([a-z_]+)"', block.group(1))) == SIGNAL_COLUMNS_030
