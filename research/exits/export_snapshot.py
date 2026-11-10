"""Run inside the demo Django container; emits research data only."""
import os, json
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'ramon_django.settings')
import django
django.setup()
from dataclasses import asdict
from django.utils import timezone
from ea_bridge.models import EAHeartbeat, MarketBar
from ea_bridge.decision import _build_pipeline
from ramon_pipeline.signalflow.replay import replay_strategy
h = EAHeartbeat.objects.get(instance_id='ramon-command-ea-1')
assert h.payload['account_trade_mode'] == 0
q, info = h.payload['quote'], h.payload['symbol_info']
p = float(q['point'])
account = dict(symbol=h.symbol, account_balance=10000, point=p,
    point_value_per_lot=float(info['tick_value'])*p/float(info['tick_size']),
    volume_step=float(info['volume_step']), min_volume=float(info['volume_min']),
    max_volume=float(info['volume_max']), stop_level_points=float(info['stops_level_points']))
now = timezone.now().timestamp()
def load(tf, limit):
    interval = {'M15':900,'M5':300,'H1':3600}[tf]
    rows = list(MarketBar.objects.filter(source=h.instance_id,symbol=h.symbol,timeframe=tf).order_by('-timestamp')[:limit])
    return [dict(time=r.timestamp.timestamp(),open=float(r.open),high=float(r.high),low=float(r.low),close=float(r.close),closed=True)
            for r in reversed(rows) if r.timestamp.timestamp()+interval <= now]
raw = load('M15',20000)
bars = [b for b in raw if b['time']%900 == 0]
context = {tf:load(tf,20000) for tf in ['H1','M5']}
pipe = _build_pipeline(demo_mode=True)
report = replay_strategy(lambda:_build_pipeline(demo_mode=True),bars,context_bars=context,account=account,
    spread_points=50,slippage_points=5,commission_points=0)
print(json.dumps(dict(source=h.instance_id,symbol=h.symbol,exported_at=timezone.now().isoformat(),
    excluded_off_grid=len(raw)-len(bars),bars=bars,context=context,account=account,
    config=dict(risk=asdict(pipe.risk_manager.config),maximum_risk_percent=pipe.risk_manager.maximum_risk_percent,
    opportunity=asdict(pipe.opportunities.config) if hasattr(pipe,'opportunities') else {}),baseline=report),default=str))
