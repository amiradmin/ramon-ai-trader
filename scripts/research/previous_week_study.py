"""Descriptive read-only comparison of the demo selector with/without weekly bias."""
import copy
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from django.test import override_settings
from ea_bridge.decision import _build_pipeline
from ea_bridge.models import MarketBar
from ea_bridge.policy_snapshot import effective_profiles, fingerprint
from ramon_pipeline.signalflow.replay import replay_strategy

SOURCE = 'litefinance-mt5-live-export-20261122'
START = datetime(2025, 6, 1, tzinfo=timezone.utc)
END = datetime(2025, 9, 1, tzinfo=timezone.utc)
ACCOUNT = {'symbol':'XAUUSD_l', 'account_balance':1000, 'point':.01,
           'point_value_per_lot':1, 'volume_step':.01, 'min_volume':.01,
           'max_volume':100, 'stop_level_points':0}

def convert(rows):
    return [dict(time=int(r.timestamp.timestamp()), open=float(r.open), high=float(r.high),
                 low=float(r.low), close=float(r.close), closed=True) for r in rows]

def main():
    qs = MarketBar.objects.filter(source=SOURCE, symbol='XAUUSD_l', timeframe='M15')
    warmup = list(qs.filter(timestamp__lt=START).order_by('-timestamp')[:256])[::-1]
    selected = list(qs.filter(timestamp__gte=START, timestamp__lt=END).order_by('timestamp'))
    bars = convert(warmup + selected)
    weeks = convert(qs.filter(timestamp__gte=START-timedelta(days=14), timestamp__lt=END).order_by('timestamp'))
    digest = hashlib.sha256(json.dumps(weeks, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    with override_settings(RAMON_DEDUPE_SIGNALS=False, RAMON_DEMO_PROFILE='expanded'):
        profiles = effective_profiles()
        template = _build_pipeline(demo_mode=True)
        if not template.compare_candidates:
            raise ValueError('Expanded demo candidate comparison must be enabled')
        lock = template.opportunities._lock
        report = {'source':SOURCE, 'timeframe':'M15', 'start':START.isoformat(), 'end_exclusive':END.isoformat(),
                  'history_fingerprint':digest, 'profiles':profiles, 'profile_fingerprint':fingerprint(profiles),
                  'baseline_and_candidate':{}}
        for enabled in (False, True):
            template.use_previous_week = enabled
            result = replay_strategy(lambda: copy.deepcopy(template, {id(lock):lock}), bars,
                context_bars={'WEEK_M15':weeks}, account=ACCOUNT, spread_points=42,
                slippage_points=5, commission_points=5, max_holding_bars=24,
                evaluation_start_time=START.timestamp(), evaluation_end_time=END.timestamp())
            name = 'weekly' if enabled else 'baseline'
            report['baseline_and_candidate'][name] = result
            Path('/tmp/ramon-previous-week-study.json').write_text(json.dumps(report, indent=2, allow_nan=False))
            print(name, json.dumps({k:result[k] for k in ('trade_count','net_r','expectancy_r','win_rate',
                 'ending_balance','max_closed_equity_drawdown_percent','context_status_counts')}), flush=True)

main()
