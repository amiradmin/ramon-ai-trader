"""Read-only, chronological standard-policy threshold study; run inside Django container."""
import copy
import json
from datetime import datetime, timezone
from django.test import override_settings
from ea_bridge.models import MarketBar
from ea_bridge.decision import _build_pipeline
from ea_bridge.policy_snapshot import effective_profiles, fingerprint
from ramon_pipeline.signalflow.replay import replay_strategy

SOURCE = "litefinance-mt5-live-export-20261122"
ACCOUNT = {"symbol": "XAUUSD_l", "account_balance": 1000, "point": .01,
           "point_value_per_lot": 1, "volume_step": .01, "min_volume": .01,
           "max_volume": 100, "stop_level_points": 0}
THRESHOLDS = (0.55, 0.65, 0.75)

def epoch(s):
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc).timestamp()

def load(start, end):
    start_dt = datetime.fromtimestamp(epoch(start), timezone.utc)
    end_dt = datetime.fromtimestamp(epoch(end), timezone.utc)
    qs = MarketBar.objects.filter(source=SOURCE, symbol="XAUUSD_l", timeframe="M15")
    prior = list(qs.filter(timestamp__lt=start_dt).order_by("-timestamp")[:256])[::-1]
    rows = prior + list(qs.filter(timestamp__gte=start_dt, timestamp__lt=end_dt).order_by("timestamp"))
    bars = [dict(time=int(r.timestamp.timestamp()), open=float(r.open), high=float(r.high),
                 low=float(r.low), close=float(r.close), closed=True) for r in rows]
    return bars

def run(template, threshold, start, end):
    bars = load(start, end)
    template.opportunities.minimum_score = threshold
    lock = template.opportunities._lock
    result = replay_strategy(lambda: copy.deepcopy(template, {id(lock): lock}), bars, context_bars={}, account=ACCOUNT,
        spread_points=42, slippage_points=5, commission_points=5, max_holding_bars=24,
        evaluation_start_time=epoch(start), evaluation_end_time=epoch(end))
    result['data'] = {'bars_including_warmup': len(bars), 'start': start, 'end_exclusive': end,
                      'gaps_over_15m': sum(b['time']-a['time']>900 for a,b in zip(bars,bars[1:]))}
    return result

def main():
    with override_settings(RAMON_DEDUPE_SIGNALS=False):
        profiles = effective_profiles()
        template = _build_pipeline(demo_mode=False)
        print('profiles', json.dumps(profiles), 'fingerprint', fingerprint(profiles), flush=True)
        development = {}
        for value in THRESHOLDS:
            result = run(template, value, '2024-02-01', '2024-05-01')
            development[str(value)] = result
            print('development', value, json.dumps(result), flush=True)
        # Require at least ten closed trades; select highest net expectancy, deterministic tie break.
        eligible = [(r['expectancy_r'], -v, v) for v in THRESHOLDS
                    if (r := development[str(v)])['trade_count'] >= 10]
        if not eligible:
            print('NO_CANDIDATE: fewer than ten trades for every threshold', flush=True)
            return
        selected = max(eligible)[2]
        print('FROZEN_THRESHOLD', selected, flush=True)
        for value in sorted(set((0.55, selected))):
            result = run(template, value, '2025-02-01', '2025-05-01')
            print('evaluation', value, json.dumps(result), flush=True)

main()
