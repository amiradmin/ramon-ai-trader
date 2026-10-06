from dataclasses import replace
import pytest
from ramon.core import Bar, Market
from ramon.market_state import assess_market, apply_market_policy


def market(closes=None, price=None):
    closes = closes or [100.] * 128
    bars = tuple(Bar(1800000000+i*900, c, c+.5, c-.5, c) for i,c in enumerate(closes))
    bid = closes[-1] if price is None else price
    return Market('XAUUSD_l', 'M15', bid, bid+.1, .01, bars)


def test_uncertain_is_explicit_and_does_not_invent_direction():
    a = assess_market(market())
    assert a['state']=='UNCERTAIN'
    assert a['route']=='WAIT' and not a['allowed_directions']


@pytest.mark.parametrize('sign,expected', [(1,'TREND_UP'), (-1,'TREND_DOWN')])
def test_directional_trend_and_pullback(sign, expected):
    m = market([100+sign*i*.1 for i in range(128)])
    assert assess_market(m)['state']==expected
    pullback = replace(m, bid=m.bid-sign*.2, ask=m.ask-sign*.2)
    assert assess_market(pullback)['state']==expected.replace('TREND','PULLBACK')


@pytest.mark.parametrize('price,expected', [(99.1,'RANGE_LOW'), (100.,'RANGE_MIDDLE'), (100.9,'RANGE_HIGH')])
def test_range_boundaries_and_middle(price, expected):
    m=market([100+[0.,.5,0.,-.5][i%4] for i in range(128)], price)
    assert assess_market(m)['state']==expected


def test_gap_shock_liquidity_and_flat_have_priority():
    m=market()
    assert assess_market(replace(m, bid=104., ask=104.1))['state']=='PRICE_GAP'
    wide=replace(m, ask=m.bid+.6)
    assert assess_market(wide)['state']=='LOW_LIQUIDITY'
    last=replace(m.bars[-1], high=102., low=98.)
    assert assess_market(replace(m, bars=(*m.bars[:-1],last)))['state']=='VOLATILITY_SHOCK'
    flat=tuple(replace(b, high=b.close, low=b.close) for b in m.bars)
    assert assess_market(replace(m,bars=flat))['state']=='FLAT_MARKET'


@pytest.mark.parametrize('sign,label', [(1,'UP'),(-1,'DOWN')])
def test_breakout_false_breakout_and_retest(sign,label):
    m=market()
    last=replace(m.bars[-1], open=100., close=100.+sign*.8,
                 high=101. if sign==1 else 100.5, low=99. if sign==-1 else 99.5)
    changed=replace(m,bars=(*m.bars[:-1],last),bid=last.close,ask=last.close+.1)
    assert assess_market(changed)['state']=='BREAKOUT_'+label
    fake=replace(last,close=100.)
    changed=replace(changed,bars=(*m.bars[:-1],fake),bid=100.,ask=100.1)
    assert assess_market(changed)['state']=='FALSE_BREAKOUT_'+label
    previous=replace(m.bars[-2],close=100.+sign*.8,high=101. if sign==1 else 100.5,low=99. if sign==-1 else 99.5)
    retest=replace(last,low=100.5 if sign==1 else 99.,high=101. if sign==1 else 99.5,open=100.+sign*.6)
    changed=replace(m,bars=(*m.bars[:-2],previous,retest),bid=retest.close,ask=retest.close+.1)
    assert assess_market(changed)['state']=='BREAKOUT_RETEST_'+label


@pytest.mark.parametrize('state', ['UNCERTAIN','PRICE_GAP','VOLATILITY_SHOCK','LOW_LIQUIDITY','REGIME_TRANSITION','DISORDERLY_MARKET','VOLATILITY_COMPRESSION'])
def test_wait_policy_cannot_be_bypassed_by_any_model_or_range(state):
    a={'state':state,'version':'test','route':'WAIT','allowed_directions':[]}
    r={'decision':'BUY','reason':'forecast_up','range_execution':1,
       'intrabar_confirmed':1,'ai_trend_confirmed':1,'intrabar_direction':'BUY','ai_trend_direction':'BUY'}
    apply_market_policy(r,a)
    assert r['decision']=='WAIT' and r['range_execution']==0


@pytest.mark.parametrize('side',['BUY','SELL'])
def test_range_middle_requires_full_confirmation_instead_of_hard_wait():
    a={'state':'RANGE_MIDDLE','version':'test','route':'CONFIRMED_MODEL','allowed_directions':['BUY','SELL']}
    r={'decision':'BUY','reason':'forecast_up','range_execution':0,
       'intrabar_confirmed':1,'ai_trend_confirmed':1,'intrabar_direction':'BUY','ai_trend_direction':'BUY'}
    apply_market_policy(r,a)
    assert r['decision']=='BUY'
    r.update(decision='BUY', intrabar_confirmed=0)
    apply_market_policy(r,a)
    assert r['decision']=='WAIT'


def test_direction_confirmation_and_range_routes(side):
    a={'state':'TREND_UP','version':'test','route':'CONFIRMED_MODEL','allowed_directions':[side]}
    r={'decision':side,'reason':'forecast','range_execution':0,'intrabar_confirmed':1,
       'ai_trend_confirmed':1,'intrabar_direction':side,'ai_trend_direction':side}
    apply_market_policy(r,a)
    assert r['decision']==side
    r['intrabar_direction']='NONE'
    apply_market_policy(r,a)
    assert r['decision']=='WAIT'
    a.update(route='RANGE_REVERSAL')
    r.update(decision=side,range_execution=1)
    apply_market_policy(r,a)
    assert r['decision']==side
    r.update(decision=side,range_execution=0)
    apply_market_policy(r,a)
    assert r['decision']=='WAIT'


def test_transition_compression_and_disorder_have_measured_evidence():
    values=[100+i*.1 for i in range(128)]
    values[-3:]=[112.0,111.6,111.2]
    assert 'REGIME_TRANSITION' in assess_market(market(values))['conditions']
    m=market()
    quiet=tuple(replace(b,high=b.close+.1,low=b.close-.1) for b in m.bars[-14:])
    a=assess_market(replace(m,bars=(*m.bars[:-14],*quiet),ask=m.bid+.01))
    assert a['state']=='VOLATILITY_COMPRESSION' and a['route']=='WAIT'
    messy=tuple(replace(b,high=104.,low=96.) if i%3==0 else b for i,b in enumerate(m.bars[-12:]))
    a=assess_market(replace(m,bars=(*m.bars[:-12],*messy)))
    assert 'DISORDERLY_MARKET' in a['conditions'] and a['route']=='WAIT'


def test_default_service_policy_is_final_authority_and_is_persisted(tmp_path,monkeypatch):
    import json, sqlite3, threading
    from dataclasses import asdict
    from http.server import HTTPServer
    from urllib.request import Request,urlopen
    import ramon.server as service
    from ramon.core import Forecast,Settings
    monkeypatch.setenv('RAMON_NEWS_ENABLED','0')
    monkeypatch.setenv('RAMON_HISTORY_DB',str(tmp_path/'history.db'))
    monkeypatch.setenv('RAMON_ENSEMBLE_DIR',str(tmp_path/'empty'))
    monkeypatch.setenv('RAMON_RANGE_LIVE_ENABLED','0')
    ready=threading.Event();servers=[]
    def factory(address,handler):
        server=HTTPServer(address,handler);servers.append(server);ready.set();return server
    monkeypatch.setattr(service,'HTTPServer',factory)
    class Model:
        model_id='test/scenario'
        def forecast(self,closes,horizon):
            c=closes[-1]
            return Forecast(c+.8,c+1.2,c+1.6,tuple(c+.3*(i+1) for i in range(4)))
    worker=threading.Thread(target=service.serve,args=('127.0.0.1',0,Model(),Settings()),daemon=True)
    worker.start();assert ready.wait(5)
    server=servers[0];url=f'http://127.0.0.1:{server.server_port}'
    try:
        m=market([100+i*.1 for i in range(128)],112.8)
        quote=m.bars[-1].time+900
        micro=tuple(Bar(quote-120+i*60,c,c+.1,c-.1,c) for i,c in enumerate([112.65,112.7,112.8]))
        m=replace(m,micro_bars=micro)
        def send(m):
            data={'symbol':m.symbol,'timeframe':m.timeframe,'point':m.point,'bid':m.bid,'ask':m.ask,
                  'quote_time':quote,'bars':[asdict(b) for b in m.bars],
                  'micro_bars':[asdict(b) for b in m.micro_bars]}
            with urlopen(Request(url+'/decision',json.dumps(data).encode(),{'Content-Type':'application/json'}),timeout=5) as r:
                return json.load(r)
        result=send(m)
        assert result['market_state']=='TREND_UP' and result['decision']=='BUY'
        assert result['replay_input_saved']==1
        import time
        time.sleep(1.05)  # history schema stores one sample per symbol/capture second
        rejected=send(replace(m,bid=117.,ask=117.1))
        assert rejected['market_state']=='PRICE_GAP' and rejected['decision']=='WAIT'
        with sqlite3.connect(tmp_path/'history.db') as db:
            rows=db.execute('select model_metadata from decision_samples order by captured,rowid').fetchall()
        audits=[json.loads(row[0]) for row in rows]
        assert audits[-1]['market_assessment']['state']=='PRICE_GAP'
        assert audits[-1]['decision_audit']['final']['decision']=='WAIT'
    finally:
        server.shutdown();server.server_close();worker.join(2)


def test_conflicting_breakout_and_rejected_breakout_wait():
    m=market()
    last=replace(m.bars[-1],high=101.,low=99.,close=100.)
    a=assess_market(replace(m,bars=(*m.bars[:-1],last),bid=100.8,ask=100.9))
    assert a['state']=='CONFLICTING_STRUCTURE' and a['route']=='WAIT'
