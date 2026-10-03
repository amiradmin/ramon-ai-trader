from dataclasses import replace
import hashlib
from pathlib import Path
import sqlite3

from ramon.core import Bar, Forecast, Market, Settings
from ramon.policy_replay import payoff, compare_rows, analyze, route_recorded, RecordedForecast
from ramon.replay import replay


def prices(start=1800000000):
    return [{'time':start+i*900,'open':100.,'high':100.5,'low':99.5,'close':100.,'spread_points':10} for i in range(8)]


def test_pricing_ignores_entry_bucket_and_handles_sell_spread_and_gap():
    bars=prices();times=[b['time'] for b in bars]
    bars[0].update(high=500.,low=1.)
    r=payoff(bars,times,quote_time=times[0]+100,bid=100.,ask=100.1,side='BUY',
             stop_distance=1.,target_distance=2.,point=.01)
    assert r['exit']=='TIME' and abs(r['r']+.1)<1e-8
    bars[1].update(high=100.95,low=99.7)
    sell=payoff(bars,times,quote_time=times[0]+100,bid=100.,ask=100.1,side='SELL',
                stop_distance=1.,target_distance=2.,point=.01)
    assert sell['exit']=='SL' and sell['r']==-1.
    bars[1].update(open=97.,high=103.,low=96.)
    gap=payoff(bars,times,quote_time=times[0]+100,bid=100.,ask=100.1,side='BUY',
               stop_distance=1.,target_distance=2.,point=.01)
    assert gap['exit']=='SL' and gap['r']<-3.


def test_missing_spread_or_nonconsecutive_horizon_is_not_priced():
    bars=prices();times=[b['time'] for b in bars]
    kw=dict(quote_time=times[0]+30,bid=100.,ask=100.1,side='BUY',stop_distance=1.,target_distance=2.,point=.01)
    bars[2]['spread_points']=0
    assert payoff(bars,times,**kw) is None
    bars[2]['spread_points']=10;bars[2]['time']+=60
    assert payoff(bars,times,**kw) is None


def test_shared_routing_does_not_invent_micro_or_bypass_policy():
    bars=tuple(Bar(1800000000+i*900,100.,100.5,99.5,100.) for i in range(256))
    m=Market('XAUUSD_l','M15',100.,100.1,.01,bars)
    model=RecordedForecast(Forecast(100.8,101.2,101.6,(100.3,100.6,100.9,101.2)))
    d,a=route_recorded(m,model,Settings(),quote_time=bars[-1].time+900)
    assert d['decision']=='WAIT' and a['state']=='UNCERTAIN'
    raw,_=route_recorded(m,model,Settings(require_direction_confirmation=False,market_state_policy_enabled=False),quote_time=bars[-1].time+900)
    assert raw['decision']=='BUY'
    protected,_=route_recorded(m,model,Settings(require_direction_confirmation=False),quote_time=bars[-1].time+900)
    assert protected['decision']=='WAIT'
    replayed=replay((*bars,*(replace(b,time=bars[-1].time+(i+1)*900) for i,b in enumerate(bars[-6:]))),[10]*262,model,start=256,
                    settings=Settings(require_direction_confirmation=False))
    assert replayed.buys==0 and replayed.policy_waits>0 and replayed.missing_micro_context>0


def test_holdout_groups_same_bar_and_purges_overlapping_labels():
    bars=prices();times=[b['time'] for b in bars];rows=[]
    for i in range(6):
        for n in range(3):
            rows.append({'quote_time':times[i]+n,'signal_bar_time':times[i]-900,'sample_key':f'{i}-{n}',
                         'bid':100.,'ask':100.1,'point':.01,'assessment':{'state':'UNCERTAIN'},
                         'base':{'decision':'BUY','stop_distance':1.,'target_distance':2.},
                         'new':{'decision':'WAIT'}})
    r=compare_rows(rows,bars,holdout_fraction=.4)
    assert r['unique_signal_bars']==6
    assert r['holdout']['unique_signal_bars']==3 and r['holdout']['rows']==9
    assert r['development']['rows']==0  # four-bar purge leaves no independent development data
    assert r['holdout']['base']['trades']==1
    assert r['holdout']['base']['overlapping_skipped']>=2
    assert r['holdout']['new']['trades']==0 and not r['promotion_eligible']


def test_analysis_opens_existing_db_read_only_and_never_creates_missing_db(tmp_path):
    missing=tmp_path/'missing.sqlite3'
    try:
        analyze(missing)
    except sqlite3.OperationalError:
        pass
    assert not missing.exists()


def test_exact_inputs_are_deduplicated_and_read_only_analysis_preserves_db(tmp_path):
    import json,zlib
    from ramon.history import ensure_history_db,persist_replay_input
    db=tmp_path/'history.sqlite3';ensure_history_db(db)
    bars=tuple(Bar(1800000000+i*900,100.,100.5,99.5,100.) for i in range(256))
    quote=bars[-1].time+900
    micro=tuple(Bar(quote-180+i*60,100.,100.1,99.9,100.) for i in range(3))
    m=Market('XAUUSD_l','M15',100.,100.1,.01,bars,micro)
    f=Forecast(100.8,101.2,101.6,(100.3,100.6,100.9,101.2))
    for key in ['a','b']:
        assert persist_replay_input(db,sample_key=key,market=m,quote_time=quote,forecast=f,settings=Settings(),response={'decision':'WAIT'})
    assert not persist_replay_input(db,sample_key='a',market=m,quote_time=quote,forecast=f,settings=Settings(),response={'decision':'WAIT'})
    with sqlite3.connect(db) as con:
        assert con.execute('select count(*) from input_blobs').fetchone()[0]==1
        assert con.execute('select count(*) from inference_audit').fetchone()[0]==2
        raw=con.execute('select provenance_json from inference_audit limit 1').fetchone()[0]
        assert json.loads(raw)['request']['micro_bars'][0]['time']==micro[0].time
        assert set(json.loads(raw)['request'])=={'symbol','timeframe','bid','ask','point','quote_time','micro_bars'}
    before=hashlib.sha256(db.read_bytes()).hexdigest()
    r=analyze(db)
    assert r['rows']==2 and r['unique_signal_bars']==1
    assert hashlib.sha256(db.read_bytes()).hexdigest()==before


def test_legacy_replay_rejects_future_micro_candles():
    import pytest
    bars=tuple(Bar(1800000000+i*900,100.,100.5,99.5,100.) for i in range(262))
    quote=bars[256].time+900
    micro=tuple(Bar(quote+i*60,100.,100.1,99.9,100.) for i in range(3))
    model=RecordedForecast(Forecast(100.8,101.2,101.6,(100.3,100.6,100.9,101.2)))
    with pytest.raises(ValueError,match='future'):
        replay(bars,[10]*262,model,start=256,micro_history={bars[256].time:micro})
