"""Compare fixed and M15-open historical spreads in an isolated replay.

Run in Django with RAMON_SPREAD_CSV pointing to the exact imported export.
This script reads data only and never changes trading settings.
"""
import copy
import csv
import hashlib
import importlib.util
import json
import os
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from django.test import override_settings
from ea_bridge.decision import _build_pipeline
from ea_bridge.models import MarketBar
from ea_bridge.mt5_export import HEADER, server_to_utc
from ea_bridge.policy_snapshot import effective_profiles, fingerprint

SOURCE = 'litefinance-mt5-live-export-20261122'
SHA256 = 'c18f599a657d5dcad53bf3a9c8bcfebd09fc3a9a0055d099f33d3d2c30e1fa83'
ACCOUNT = {'symbol':'XAUUSD_l', 'account_balance':1000, 'point':.01,
           'point_value_per_lot':1, 'volume_step':.01, 'min_volume':.01,
           'max_volume':100, 'stop_level_points':0}
PERIODS = (('2024-02-01','2024-05-01'), ('2025-06-01','2025-09-01'))

def epoch(value):
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc).timestamp()

def load_csv(path):
    with path.open('rb') as f:
        checksum = hashlib.file_digest(f, 'sha256').hexdigest()
    if checksum != SHA256:
        raise ValueError('CSV checksum does not match imported source')
    opening = {}
    with path.open(encoding='utf-8-sig', newline='') as f:
        reader = csv.reader(f, delimiter='\t')
        if next(reader, None) != HEADER:
            raise ValueError('Unexpected export format')
        for row in reader:
            stamp = datetime.strptime(row[0]+' '+row[1], '%Y.%m.%d %H:%M:%S')
            if stamp.minute % 15:
                continue
            utc = int(server_to_utc(stamp).timestamp())
            if utc in opening:
                raise ValueError('Duplicate M15 opening minute')
            spread = Decimal(row[8])
            if not spread.is_finite() or spread < 0 or spread != int(spread):
                raise ValueError('Invalid spread')
            opening[utc] = (Decimal(row[2]), int(spread))
    return opening

def bars_for(start, end, opening):
    qs = MarketBar.objects.filter(source=SOURCE, symbol='XAUUSD_l', timeframe='M15')
    start_dt = datetime.fromtimestamp(epoch(start), timezone.utc)
    end_dt = datetime.fromtimestamp(epoch(end), timezone.utc)
    rows = list(qs.filter(timestamp__lt=start_dt).order_by('-timestamp')[:256])[::-1]
    rows += list(qs.filter(timestamp__gte=start_dt, timestamp__lt=end_dt).order_by('timestamp'))
    bars = []
    for row in rows:
        t = int(row.timestamp.timestamp())
        if t not in opening or opening[t][0] != row.open:
            raise ValueError(f'CSV opening minute missing or mismatched for {t}')
        bars.append(dict(time=t, open=float(row.open), high=float(row.high), low=float(row.low),
                         close=float(row.close), closed=True, spread_points=opening[t][1]))
    return bars

def main():
    path = Path(os.environ['RAMON_SPREAD_CSV'])
    replay_path = os.environ.get('RAMON_REPLAY_MODULE')
    if replay_path:
        spec = importlib.util.spec_from_file_location('ramon_pipeline.signalflow.replay_research', replay_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        replay_strategy = module.replay_strategy
    else:
        from ramon_pipeline.signalflow.replay import replay_strategy
    opening = load_csv(path)
    with override_settings(RAMON_DEDUPE_SIGNALS=False):
        profiles = effective_profiles()
        template = _build_pipeline(demo_mode=False)
        lock = template.opportunities._lock
        report = {'source':SOURCE, 'csv_sha256':SHA256, 'profiles':profiles,
                  'profile_fingerprint':fingerprint(profiles), 'periods':{}}
        for start,end in PERIODS:
            bars = bars_for(start,end,opening)
            key = start+'_'+end
            report['periods'][key] = {}
            for mode in ('fixed','bar_open'):
                result = replay_strategy(lambda: copy.deepcopy(template, {id(lock):lock}), bars,
                    context_bars={}, account=ACCOUNT, spread_points=42, spread_mode=mode,
                    slippage_points=5, commission_points=5, max_holding_bars=24,
                    evaluation_start_time=epoch(start), evaluation_end_time=epoch(end))
                result['data'] = {'bars_including_warmup':len(bars),
                    'gap_intervals':sum(b['time']-a['time']>900 for a,b in zip(bars,bars[1:]))}
                report['periods'][key][mode] = result
                Path('/tmp/ramon-historical-spread-study.json').write_text(json.dumps(report,indent=2,allow_nan=False))
                print(key, mode, json.dumps({k:result[k] for k in
                    ('trade_count','net_r','expectancy_r','win_rate','ending_balance',
                     'max_closed_equity_drawdown_percent','data')}), flush=True)

main()
