import json
import pytest
from ramon.foundation_benchmark import MODELS, score, side, summarize


def document():
    rows=[]
    for i,actual in enumerate((102,100,98)):
        rows.append({'time':i,'reference':100,'band':.5,'atr':2,
          'actual':[actual]*5,'entry_bid':100,'entry_spread':.4,'exit_spreads':[.4]*5,
          'context':[[0,100,101,99,100],[1,100,101,99,100]]})
    return {'samples':rows,'friction':.1,'scope':'test','limitations':[]}


def test_same_class_band_and_costs():
    d=document()
    result=score(d,[{'close':r['actual']} for r in d['samples']])
    assert side(100.5,100,.5)==1
    for h in ('1','3','5'):
        assert result[h]['balanced_accuracy']==1
        assert result[h]['mae_atr']==0
        assert result[h]['trades']==2
        assert result[h]['mean_net_atr']==pytest.approx(.75)


def test_missing_models_are_not_ranked_and_manifest_mismatch_rejected(tmp_path):
    manifest=tmp_path/'manifest.json';manifest.write_text(json.dumps(document()))
    report=summarize(manifest,tmp_path)
    assert set(report['unavailable'])==set(MODELS)
    assert set(report['results'])=={'persistence','last_return'}
    (tmp_path/'chronos-small.json').write_text(json.dumps({'status':'complete',
        'manifest_sha256':'wrong','predictions':[]}))
    with pytest.raises(ValueError):summarize(manifest,tmp_path)


def test_complete_manifest_result_is_reused_without_model_loading(tmp_path):
    import hashlib
    from ramon.foundation_benchmark import run_model
    manifest=tmp_path/'manifest.json';manifest.write_text(json.dumps(document()))
    output=tmp_path/'chronos-small.json'
    cached={'status':'complete','manifest_sha256':hashlib.sha256(manifest.read_bytes()).hexdigest(),
            'predictions':[]}
    output.write_text(json.dumps(cached))
    assert run_model('chronos-small',manifest,output)==cached
