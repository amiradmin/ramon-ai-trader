import json
from pathlib import Path
import pytest
pytest.importorskip('xgboost')
from ramon.xgboost_lab import fit_pair, TreeValueModel
from ramon.trade_selector_lab import SELECTOR_FEATURES, run


def test_nonlinear_native_models_json_roundtrip_and_worker_limit(tmp_path):
    import xgboost as xgb
    samples=[(i,tuple([float(i%100)/100]+[0.]*(len(SELECTOR_FEATURES)-1))) for i in range(200)]
    labels={i:{side:((1.9 if (.4<x[0]<.6)==(side=='BUY') else -1.1),i+4,'TIMEOUT')
               for side in ('BUY','SELL')} for i,x in samples}
    pair=fit_pair(samples,labels,{'max_depth':2,'rounds':40},artifact_dir=tmp_path,cpu_workers=2)
    center=[.5]+[0.]*(len(SELECTOR_FEATURES)-1)
    edge=[.05]+[0.]*(len(SELECTOR_FEATURES)-1)
    assert pair[0].predict(center)>pair[0].predict(edge)
    loaded=xgb.Booster({'nthread':2});loaded.load_model(tmp_path/'BUY.json')
    assert TreeValueModel(loaded).predict(center)==pytest.approx(pair[0].predict(center))
    meta=json.load(open(tmp_path/'metadata.json'))
    assert meta['parameters']['nthread']==2
    assert meta['model_order']==['BUY','SELL']
    with pytest.raises(ValueError):
        fit_pair(samples[:10],labels,{'max_depth':2,'rounds':10})


def test_nested_tree_selection_is_past_only_and_read_only(tmp_path,monkeypatch):
    import sqlite3
    import ramon.xgboost_lab as trees
    from test_historical_benchmark import seed
    monkeypatch.setattr(trees,'CONFIGS',({'max_depth':2,'rounds':20},))
    db=tmp_path/'input.sqlite3';seed(db,rows=12000)
    before=db.read_bytes()
    first=run(db,backend='xgboost',output_dir=tmp_path/'first',stride=128,show_progress=False)
    assert db.read_bytes()==before
    for f in first['folds']:
        assert f['last_training_label_index']<f['evaluation']['start_index']
        assert f['inner']['last_training_label_index']<f['inner']['validation_start_index']
    with sqlite3.connect(db) as conn:
        cutoff=conn.execute('SELECT time FROM history_bars ORDER BY time LIMIT 1 OFFSET 2400').fetchone()[0]
        conn.execute('UPDATE history_bars SET open=open+100,high=high+100,low=low+100,close=close+100 WHERE time>=?',(cutoff,))
    second=run(db,backend='xgboost',output_dir=tmp_path/'second',stride=128,show_progress=False)
    assert first['folds'][0]['inner_candidates']==second['folds'][0]['inner_candidates']
    assert first['folds'][0]['selected']==second['folds'][0]['selected']
    a=tmp_path/'first/fold_1_inner_models/depth_2/BUY.json'
    b=tmp_path/'second/fold_1_inner_models/depth_2/BUY.json'
    assert a.exists() and a.read_bytes()==b.read_bytes()
    assert not first['gate']['live_promotion']
