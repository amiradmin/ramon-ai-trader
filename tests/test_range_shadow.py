from dataclasses import replace
import sqlite3
from ramon.core import Bar,Market
from ramon.range_shadow import observe


def market(side='BUY'):
 bars=tuple(Bar(1800000000+i*900,100,104 if i==8 else 100.5,96 if i==8 else 99.5,100) for i in range(20))
 closes=[95.9,96.1,96.2] if side=='BUY' else [104.1,103.9,103.8]
 micro=tuple(Bar(1800020000+i*60,v,v+.1,v-.1,v) for i,v in enumerate(closes))
 return Market('XAUUSD_l','M15',closes[-1],closes[-1]+.1,.01,bars,micro)


def test_buy_persistence_spread_and_no_execution(tmp_path):
 db=str(tmp_path/'shadow.db');m=market()
 r=observe(db,m,1800021000)
 assert r['range_shadow_status']=='OPENED' and r['range_shadow_effect']=='NONE' and 'decision' not in r
 assert observe(db,m,1800021000)['range_shadow_status']=='STALE_QUOTE'
 assert observe(db,replace(m,bid=100.1,ask=100.2),1800021060)['range_shadow_status']=='CLOSED_TARGET'
 with sqlite3.connect(db) as c:
  entry,pnl=c.execute('select entry,net_price from range_shadow_trades').fetchone()
  assert abs(entry-96.3)<1e-9 and abs(pnl-3.8)<1e-9
 assert observe(db,m,1800021100)['range_shadow_status']=='COOLDOWN'


def test_sell_gap_uses_executable_ask(tmp_path):
 db=str(tmp_path/'shadow.db');m=market('SELL')
 assert observe(db,m,1800021000)['range_shadow_status']=='OPENED'
 assert observe(db,replace(m,bid=105,ask=105.1),1800021060)['range_shadow_status']=='CLOSED_STOP'
 with sqlite3.connect(db) as c:assert abs(c.execute('select net_price from range_shadow_trades').fetchone()[0]+1.3)<1e-9


def test_missing_timestamp_midrange_timeout(tmp_path):
 db=str(tmp_path/'shadow.db');m=market()
 assert observe(db,m,None)['range_shadow_status']=='MISSING_QUOTE_TIME'
 assert observe(db,replace(m,bid=100,ask=100.1),1800021000)['range_shadow_status']=='WAIT_BOUNDARY_REVERSAL'
 observe(db,m,1800021000)
 assert observe(db,replace(m,bid=97,ask=97.1),1800022800)['range_shadow_status']=='CLOSED_TIMEOUT'


def test_live_requires_capability_and_never_overrides_primary_or_veto():
 from ramon.range_strategy import live_candidate
 m=market();kwargs=dict(enabled=True,capable=True,quote_time=1800021000,max_spread_points=50)
 response={'decision':'WAIT','reason':'insufficient_model_strength'}
 assert live_candidate(m,response,**kwargs)['direction']=='BUY'
 assert live_candidate(m,response,**(kwargs|{'enabled':False})) is None
 assert live_candidate(m,response,**(kwargs|{'capable':False})) is None
 for response in [{'decision':'BUY','reason':'forecast_up'},{'decision':'WAIT','reason':'adverse_intrabar_timing'},{'decision':'WAIT','reason':'trend_conflict'}]:
  assert live_candidate(m,response,**kwargs) is None
 assert live_candidate(replace(m,ask=m.bid+1),{'decision':'WAIT','reason':'insufficient_model_strength'},**kwargs) is None


def test_http_range_protocol_and_sample_provenance(tmp_path,monkeypatch):
 import json,socket,threading,time
 from urllib.request import Request,urlopen
 from ramon.server import serve
 from ramon.core import Forecast,Settings
 monkeypatch.setenv('RAMON_RANGE_LIVE_ENABLED','1')
 monkeypatch.setenv('RAMON_HISTORY_DB',str(tmp_path/'history.db'))
 monkeypatch.setenv('RAMON_ENSEMBLE_DIR',str(tmp_path/'no_roles'))
 monkeypatch.setenv('RAMON_NEWS_ENABLED','0')
 class Fake:
  model_id='test/fake'
  def forecast(self,closes,horizon):return Forecast(90,100,110)
 with socket.socket() as s:
  s.bind(('127.0.0.1',0));port=s.getsockname()[1]
 threading.Thread(target=serve,args=('127.0.0.1',port,Fake(),Settings()),daemon=True).start()
 m=market();m=replace(m,bars=tuple(Bar(m.bars[0].time-(108-i)*900,100,100.5,99.5,100) for i in range(108))+m.bars);quote=m.bars[-1].time+900
 payload={'symbol':m.symbol,'timeframe':'M15','bid':m.bid,'ask':m.ask,'point':m.point,'quote_time':quote,
          'bars':[{'time':b.time,'open':b.open,'high':b.high,'low':b.low,'close':b.close} for b in m.bars],
          'micro_bars':[{'time':quote-120+i*60,'open':b.open,'high':b.high,'low':b.low,'close':b.close} for i,b in enumerate(m.micro_bars)]}
 url=f'http://127.0.0.1:{port}'
 for _ in range(30):
  try:
   with urlopen(url+'/health') as f:assert json.load(f)['range_main_enabled'];break
  except OSError:time.sleep(.01)
 def send(p):
  with urlopen(Request(url+'/decision',data=json.dumps(p).encode(),headers={'Content-Type':'application/json'})) as f:return json.load(f)
 assert send(payload)['decision']=='WAIT'
 time.sleep(1.05)
 result=send(payload|{'range_execution_ready':True})
 assert result['decision']=='BUY' and result['range_execution']==1
 assert result['target_tp3']==0 and result['target_method']=='range_midpoint'
 assert abs(result['range_target_price']-100)<1e-9 and result['sample_saved']==1
 with sqlite3.connect(tmp_path/'history.db') as c:
  row=c.execute('select chronos_model,direction,stop_distance,model_metadata from decision_samples where sample_key=?',(result['sample_key'],)).fetchone()
  assert row[0]=='range-reversal-v1' and row[1]=='BUY' and row[2]>0
  assert json.loads(row[3])['execution_strategy']=='range-reversal-v1'
