"""Research-only setup selection study. Run under Django; never writes policy settings."""
import copy
import json
from datetime import datetime, timezone
from pathlib import Path
from django.test import override_settings
from ea_bridge.models import MarketBar
from ea_bridge.decision import _build_pipeline
from ea_bridge.policy_snapshot import effective_profiles, fingerprint
from ramon_pipeline.signalflow.replay import replay_strategy

SOURCE = 'litefinance-mt5-live-export-20261122'
ACCOUNT = {'symbol':'XAUUSD_l', 'account_balance':1000, 'point':.01,
           'point_value_per_lot':1, 'volume_step':.01, 'min_volume':.01,
           'max_volume':100, 'stop_level_points':0}
VARIANTS = {'baseline': None, 'trend_only': 'trend_pullback', 'breakout_only': 'breakout_retest'}
OUTPUT = Path('/tmp/ramon-setup-rule-study.json')

def epoch(value):
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc).timestamp()

def bars_for(start, end):
    qs = MarketBar.objects.filter(source=SOURCE, symbol='XAUUSD_l', timeframe='M15')
    start_dt = datetime.fromtimestamp(epoch(start), timezone.utc)
    end_dt = datetime.fromtimestamp(epoch(end), timezone.utc)
    rows = list(qs.filter(timestamp__lt=start_dt).order_by('-timestamp')[:256])[::-1]
    rows += list(qs.filter(timestamp__gte=start_dt, timestamp__lt=end_dt).order_by('timestamp'))
    bars = [dict(time=int(r.timestamp.timestamp()), open=float(r.open), high=float(r.high),
                 low=float(r.low), close=float(r.close), closed=True) for r in rows]
    return bars

def simulate(template, name, bars, start, end):
    pipeline = copy.deepcopy(template, {id(template.opportunities._lock): template.opportunities._lock})
    chosen = VARIANTS[name]
    if chosen:
        for key, setup in pipeline.opportunities.trading_settings['opportunity_finder']['setups'].items():
            setup['enabled'] = bool(setup['enabled'] and key == chosen)
    lock = pipeline.opportunities._lock
    result = replay_strategy(lambda: copy.deepcopy(pipeline, {id(lock): lock}), bars,
        context_bars={}, account=ACCOUNT, spread_points=42, slippage_points=5,
        commission_points=5, max_holding_bars=24,
        evaluation_start_time=epoch(start), evaluation_end_time=epoch(end))
    result['data'] = {'start':start, 'end_exclusive':end, 'bars_including_warmup':len(bars),
                      'gap_intervals':sum(b['time'] - a['time'] > 900 for a,b in zip(bars,bars[1:]))}
    return result

def summary(result):
    trades = result['trades']
    groups = {}
    for t in trades:
        hour = datetime.fromtimestamp(t['entry_time'], timezone.utc).hour
        for key in ('setup:'+t['setup'], 'side:'+t['side'], 'exit:'+t['exit_reason'],
                    'utc_hour_block:'+str(hour//6*6)):
            g = groups.setdefault(key, {'trades':0,'net_r':0.0,'pnl':0.0})
            g['trades'] += 1; g['net_r'] += t['net_r']; g['pnl'] += t['pnl']
    return {'trade_count':result['trade_count'], 'expectancy_r':result['expectancy_r'],
            'net_r':result['net_r'], 'ending_balance':result['ending_balance'],
            'drawdown':result['max_closed_equity_drawdown_percent'],
            'win_rate':result['win_rate'], 'groups':groups, 'data':result['data']}

def save(report):
    OUTPUT.write_text(json.dumps(report, indent=2, allow_nan=False))

def main():
    with override_settings(RAMON_DEDUPE_SIGNALS=False):
        profiles = effective_profiles()
        template = _build_pipeline(demo_mode=False)
        report = {'profiles':profiles,'profile_fingerprint':fingerprint(profiles),
                  'source':SOURCE,'account':ACCOUNT,'development':{},'evaluation':{}}
        development = bars_for('2024-02-01','2024-05-01')
        for name in VARIANTS:
            r = simulate(template, name, development, '2024-02-01','2024-05-01')
            report['development'][name] = r; save(report)
            print('development', name, json.dumps(summary(r)), flush=True)
        # Candidate must have positive expectancy and at least ten trades.
        eligible = [(r['expectancy_r'], name) for name,r in report['development'].items()
                    if name != 'baseline' and r['trade_count'] >= 10 and r['expectancy_r'] > 0]
        if not eligible:
            print('NO_ELIGIBLE_CANDIDATE', flush=True); return
        candidate = max(eligible)[1]
        report['frozen_candidate'] = candidate; save(report)
        print('FROZEN_CANDIDATE', candidate, flush=True)
        evaluation = bars_for('2025-06-01','2025-09-01')
        for name in ('baseline', candidate):
            r = simulate(template, name, evaluation, '2025-06-01','2025-09-01')
            report['evaluation'][name] = r; save(report)
            print('evaluation', name, json.dumps(summary(r)), flush=True)

main()
