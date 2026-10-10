from dataclasses import replace, asdict
import json
import threading
from http.server import HTTPServer
from urllib.request import urlopen, Request
import pytest
from ramon.core import Market, Forecast, Settings, evaluate
from ramon.reversal_strategy import apply_reversal
from test_trend_conflict import falling_bars, reversal_micro_bars, FixedModel


def setup():
    bars=falling_bars()
    quote=bars[-1].time+1000
    micro=tuple(replace(b,time=quote//60*60-(3-i)*60) for i,b in enumerate(reversal_micro_bars()))
    market=Market('XAUUSD_l','M15',100,100.4,.01,bars,micro)
    model=FixedModel(Forecast(95,103,105,(100.5,101.2,102.1,103)))
    model.model_id='test-model'
    base=evaluate(market,model)
    return market,model,base,quote


def assessment(state='TREND_DOWN',route='CONFIRMED_MODEL'):
    return {'state':state,'route':route,'version':'market-state-v1','allowed_directions':['SELL']}


def test_opt_in_reversal_has_normal_strength_and_both_confirmations():
    _,_,base,_=setup(); original=base.to_dict();response=base.to_dict()
    assert apply_reversal(response,base,Settings(),assessment(),enabled=True)
    assert response['decision']=='BUY' and response['reversal_execution']==1
    assert response['market_state_route']=='CONFIRMED_REVERSAL'
    assert response['range_execution']==0
    assert base.to_dict()==original and base.decision=='WAIT'


@pytest.mark.parametrize('changed', [
    {'signal_strength':.19}, {'intrabar_confirmed':0}, {'ai_trend_confirmed':0},
    {'intrabar_direction':'SELL'}, {'ai_trend_direction':'SELL'},
    {'buy_edge':.01,'sell_edge':-.02}, {'reason':'spread_or_atr'},
    {'trend_conflict_active':0}, {'intrabar_move_atr':3.0},
])
def test_reversal_does_not_bypass_other_failed_requirements(changed):
    _,_,base,_=setup();base=replace(base,**changed);response=base.to_dict()
    assert not apply_reversal(response,base,Settings(),assessment(),enabled=True)
    assert response['decision']=='WAIT'


@pytest.mark.parametrize('state,route', [('VOLATILITY_SHOCK','WAIT'),('REGIME_TRANSITION','WAIT'),('PRICE_GAP','WAIT'),('RANGE_LOW','RANGE_REVERSAL'),('TREND_UP','CONFIRMED_MODEL')])
def test_reversal_retains_hazard_and_range_routing(state,route):
    _,_,base,_=setup();response=base.to_dict()
    assert not apply_reversal(response,base,Settings(),assessment(state,route),enabled=True)
    assert response['decision']=='WAIT'


def test_default_and_manual_pass_cannot_activate_reversal():
    _,_,base,_=setup()
    for enabled,manual in [(False,frozenset()),(True,frozenset({'timing'}))]:
        response=base.to_dict()
        assert not apply_reversal(response,base,Settings(),assessment(),enabled=enabled,manual_overrides=manual)
        assert response['decision']=='WAIT'


@pytest.mark.parametrize('enabled,cent,expected', [(False,False,'WAIT'),(True,False,'BUY'),(True,True,'WAIT')])
def test_http_route_remains_gated_and_never_submits_an_order(tmp_path,monkeypatch,enabled,cent,expected):
    import ramon.server as server
    monkeypatch.setenv("RAMON_AI_ENGINE_V2_ENABLED", "0")
    monkeypatch.setenv("RAMON_ROLE_MODE", "live")
    market,model,base,quote=setup()
    for name in ['RAMON_MOMENT_SHADOW_ENABLED','RAMON_FINBERT_SHADOW_ENABLED','RAMON_TIMESFM3_SHADOW_ENABLED','RAMON_NEWS_ENABLED','RAMON_RANGE_LIVE_ENABLED']:
        monkeypatch.setenv(name,'0')
    monkeypatch.setenv('RAMON_REVERSAL_LIVE_ENABLED','1' if enabled else '0')
    monkeypatch.setenv('RAMON_HISTORY_DB',str(tmp_path/'history.db'))
    monkeypatch.setenv('RAMON_ENSEMBLE_DIR',str(tmp_path/'missing-models'))
    monkeypatch.setattr(server,'assess_market',lambda *a,**k:assessment())
    # Force an independent direction conflict to exercise the downstream cent gate.
    monkeypatch.setattr(server,'independent_market_direction',lambda *a,**k:{'direction':'SELL','score':-2,'ret_1_atr':0,'ret_4_atr':0,'micro_move_atr':0,'structure':'SELL'})
    ready=threading.Event();servers=[]
    def factory(address,handler):
        http=HTTPServer(('127.0.0.1',0),handler);servers.append(http);ready.set();return http
    monkeypatch.setattr(server,'HTTPServer',factory)
    thread=threading.Thread(target=server.serve,args=('127.0.0.1',0,model,Settings()),daemon=True);thread.start()
    assert ready.wait(5)
    http=servers[0]
    try:
        payload=asdict(market)|{'quote_time':quote,'account_is_cent':cent}
        request=Request(f'http://127.0.0.1:{http.server_port}/decision',data=json.dumps(payload).encode(),headers={'Content-Type':'application/json'})
        with urlopen(request,timeout=5) as result:response=json.load(result)
        assert response['decision']==expected
        if enabled and not cent:
            assert response['reason']=='confirmed_countertrend_reversal'
            assert response['sample_saved']==1
        if enabled and cent:
            assert response['reason']=='market_direction_conflict'
            assert response['reversal_execution']==0
    finally:
        http.shutdown();http.server_close();thread.join(5)
