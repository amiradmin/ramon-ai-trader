from dataclasses import replace
import json
import sqlite3

import pytest

from ramon.core import Bar
from ramon.historical_benchmark import m15_gap_prefix
from ramon.smc_local_lab import FEATURE_NAMES, fit, features, training_indices, run, promotion_gate


def test_training_labels_are_purged_and_gaps_excluded():
    bars = [Bar(i*900,100,101,99,100) for i in range(400)]
    bars = [replace(b,time=b.time+(900 if i>=298 else 0)) for i,b in enumerate(bars)]
    indices = training_indices(bars,m15_gap_prefix(bars),end=310,horizon=4,stride=1)
    assert max(indices)+4 < 310
    assert not set(range(294,298)) & set(indices)
    assert 298 in indices


def test_features_use_past_only_and_are_price_translation_invariant():
    bars = [Bar(i*900,100+i*.1,101+i*.1,99+i*.1,100.2+i*.1) for i in range(256)]
    shifted = [replace(b,open=b.open+1000,high=b.high+1000,low=b.low+1000,close=b.close+1000) for b in bars]
    assert features(bars) == pytest.approx(features(shifted),abs=1e-10)


def test_ridge_scaling_uses_training_only():
    rows = [tuple(float(i%13+j*.1) for j in range(len(FEATURE_NAMES))) for i in range(120)]
    targets = [row[0]*.2-.5 for row in rows]
    model = fit(rows,targets,l2=1,metadata={})
    before = model.means
    assert model.predict(rows[0]) < model.predict(rows[12])
    model.predict([10000]*len(FEATURE_NAMES))
    assert model.means == before
    with pytest.raises(ValueError):
        fit(rows[:10],targets[:10],l2=1,metadata={})


def test_walk_forward_runs_without_input_or_live_mutation(tmp_path):
    from test_historical_benchmark import seed
    db = tmp_path/'input.sqlite3'
    seed(db,rows=9000)
    before = db.read_bytes()
    shadow = tmp_path/'shadow.sqlite3'
    result = run(db,stride=64,train_stride=1,output_dir=tmp_path/'out',
                 shadow_db=shadow,show_progress=False)
    assert db.read_bytes() == before
    assert len(result['folds']) == 5
    for fold in result['folds']:
        train = fold['training']
        assert train['training_last_label_index'] < fold['evaluation']['start_index']
        assert set(fold['models']) == {'SMC_LOCAL','previous_bar','momentum_4bar',
                                      'contrarian_previous_bar','contrarian_momentum_4bar'}
        assert fold['models']['SMC_LOCAL']['gap_skipped'] == 0
        assert json.load(open(fold['model_artifact']))['schema'] == 'smc-local-atr-v1'
    if not result['gate']['eligible_for_shadow']:
        assert not shadow.exists()
    assert result['gate']['live_promotion'] is False


def test_gate_rejects_sparse_or_negative_folds():
    metrics = {'trades':100,'mean_r':.1,'profit_factor':1.2}
    folds = [{'models':{'SMC_LOCAL':{'metrics':dict(metrics)},
                       'baseline':{'metrics':{'mean_r':-.1}}}} for _ in range(5)]
    assert promotion_gate(folds)['eligible_for_shadow']
    folds[2]['models']['SMC_LOCAL']['metrics']['mean_r'] = -.1
    assert not promotion_gate(folds)['eligible_for_shadow']


def test_first_fold_model_is_unchanged_by_future_prices(tmp_path):
    from test_historical_benchmark import seed
    db = tmp_path/'input.sqlite3'
    seed(db,rows=2400)
    first = run(db,stride=128,train_stride=1,output_dir=tmp_path/'first',show_progress=False)
    with sqlite3.connect(db) as conn:
        first_time = conn.execute('SELECT time FROM history_bars ORDER BY time LIMIT 1 OFFSET 480').fetchone()[0]
        conn.execute('UPDATE history_bars SET open=open+100, high=high+100, low=low+100, close=close+100 WHERE time>=?',(first_time,))
    second = run(db,stride=128,train_stride=1,output_dir=tmp_path/'second',show_progress=False)
    a = json.load(open(first['folds'][0]['model_artifact']))['model']
    b = json.load(open(second['folds'][0]['model_artifact']))['model']
    # Provenance changes when the whole dataset changes, fitted parameters do not.
    a.pop('metadata'); b.pop('metadata')
    assert a == b


def test_shadow_cannot_target_input_or_existing_database(tmp_path):
    db = tmp_path/'input.sqlite3'
    db.touch()
    with pytest.raises(ValueError,match='separate'):
        run(db,shadow_db=db)
    existing = tmp_path/'existing.sqlite3'
    existing.touch()
    with pytest.raises(ValueError,match='new dedicated'):
        run(db,shadow_db=existing)
