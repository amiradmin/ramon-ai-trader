import json
from dataclasses import replace
import pytest
from ramon.core import Bar
from ramon.trade_selector_lab import outcome, choose, replay, run


def test_cost_spread_and_stop_first():
    bars=[Bar(i*900,100,101,99,100) for i in range(270)]
    bars[257]=replace(bars[257],high=110,low=90)
    for side in ('BUY','SELL'):
        r,closed,kind=outcome(bars,[42]*270,256,side)
        assert (r,closed,kind)==(-1.1,257,'LOSS')
    bars=[Bar(i*900,100,101,99,100) for i in range(270)]
    buy=outcome(bars,[42]*270,256,'BUY')[0]
    sell=outcome(bars,[42]*270,256,'SELL')[0]
    assert buy==pytest.approx(-.42/3-.1)
    assert sell==pytest.approx(buy)


def test_policy_waits_for_positive_net_expectation():
    class Fixed:
        def __init__(self,v): self.v=v
        def predict(self,x):return self.v
    assert choose((Fixed(-.1),Fixed(-.2)),[],0)=='WAIT'
    assert choose((Fixed(.2),Fixed(.1)),[],.05)=='BUY'
    assert choose((Fixed(.1),Fixed(.2)),[],.05)=='SELL'


def test_nonoverlap():
    samples=[(i,[]) for i in range(5)]
    labels={i:{'BUY':(.1,i+2,'TIMEOUT')} for i in range(5)}
    trades,counts=replay(samples,labels,lambda i,x:'BUY')
    assert len(trades)==2


def test_nested_research_preserves_input_and_purges_labels(tmp_path):
    from test_historical_benchmark import seed
    db=tmp_path/'input.sqlite3';seed(db,rows=10000)
    original=db.read_bytes()
    r=run(db,output_dir=tmp_path/'out',stride=128,train_stride=16,show_progress=False)
    assert db.read_bytes()==original
    assert len(r['folds'])==5
    assert not r['gate']['live_promotion']
    for f in r['folds']:
        assert f['last_training_label_index']<f['evaluation']['start_index']
        inner=f['inner']
        assert inner['last_training_label_index']<inner['validation_start_index']
        if f['selected'] is None:
            assert f['models']['SELECTOR']['metrics']['trades']==0


@pytest.mark.parametrize('side',['BUY','SELL'])
@pytest.mark.parametrize('exit_case',['stop','target','both','timeout'])
def test_labels_match_existing_execution_simulator(monkeypatch,side,exit_case):
    from dataclasses import replace
    from types import SimpleNamespace
    from ramon.core import Settings, atr14
    import ramon.historical_benchmark as lab
    bars=[Bar(i*900,100,101,99,100) for i in range(270)]
    if exit_case=='both': high,low=110,90
    elif exit_case=='target': high,low=(107,99) if side=='BUY' else (101,93)
    elif exit_case=='stop': high,low=(101,90) if side=='BUY' else (110,99)
    else: high,low=101,99
    bars[257]=replace(bars[257],high=high,low=low)
    spreads=[42]*270;spreads[257]=50
    monkeypatch.setattr(lab,'evaluate',lambda market,model,settings:SimpleNamespace(
        decision=side,stop_distance=1.5*atr14(market.bars),target_distance=3*atr14(market.bars)))
    trades,_=lab.simulate(bars,spreads,None,symbol='XAUUSD_KAGGLE',point=.01,
        settings=replace(Settings(),horizon=4),start=256,end=261,stride=1,
        fallback_spread_points=42,roundtrip_cost_r=.1)
    r,closed,kind=outcome(bars,spreads,256,side)
    assert len(trades)==1
    assert trades[0].r==pytest.approx(r)
    assert trades[0].outcome==kind


def test_inner_selection_ignores_outer_future_prices(tmp_path):
    import sqlite3
    from test_historical_benchmark import seed
    db=tmp_path/'input.sqlite3';seed(db,rows=12000)
    first=run(db,output_dir=tmp_path/'first',stride=128,train_stride=4,show_progress=False)
    with sqlite3.connect(db) as conn:
        cutoff=conn.execute('SELECT time FROM history_bars ORDER BY time LIMIT 1 OFFSET 2400').fetchone()[0]
        conn.execute('UPDATE history_bars SET open=open+100,high=high+100,low=low+100,close=close+100 WHERE time>=?',(cutoff,))
    second=run(db,output_dir=tmp_path/'second',stride=128,train_stride=4,show_progress=False)
    assert first['folds'][0]['inner_candidates']
    assert first['folds'][0]['inner_candidates']==second['folds'][0]['inner_candidates']
    assert first['folds'][0]['selected']==second['folds'][0]['selected']
