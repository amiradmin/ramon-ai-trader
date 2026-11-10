"""Reproducible descriptive replay. No order or observation writes."""
from dataclasses import asdict, replace
from datetime import timedelta
import hashlib
import json
from pathlib import Path
import subprocess

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from ea_bridge.models import EAHeartbeat, MarketBar, DecisionThresholdConfig
from ea_bridge.scalp_observer import candle
from ramon_pipeline.signalflow import trend_shadow
from ramon_pipeline.signalflow.trend_shadow import TrendCosts, TrendPolicy, aggregate_h4, replay_trend
from ramon_pipeline.signalflow.scalping import valid_bar


class Command(BaseCommand):
    help='Read-only descriptive H1/H4 replay with costs and a no-trade baseline.'

    def add_arguments(self,parser):
        parser.add_argument('--source',required=True)
        parser.add_argument('--symbol',default='XAUUSD_o')
        parser.add_argument('--limit',type=int,default=5000)
        for cost in ['spread','slippage','commission','overnight']:
            parser.add_argument(f'--{cost}-points',type=float,required=True)
        parser.add_argument('--rollover-utc-hour',type=int,default=22)
        parser.add_argument('--snapshot-output')

    def handle(self,*args,**options):
        if not 256<=options['limit']<=20000: raise CommandError('limit must be 256–20000')
        feed=EAHeartbeat.objects.filter(instance_id=options['source'],symbol=options['symbol']).first()
        if not feed or type(feed.payload.get('account_trade_mode')) is not int or feed.payload['account_trade_mode']!=0:
            raise CommandError('Explicit matching demo feed required')
        now=timezone.now()
        try:
            costs=TrendCosts(float(feed.payload['quote']['point']),options['spread_points'],options['slippage_points'],
                options['commission_points'],options['overnight_points'],options['rollover_utc_hour'])
            tick=float(feed.payload['symbol_info']['tick_size'])
        except (KeyError,TypeError,ValueError) as exc: raise CommandError(str(exc)) from exc
        rows=list(MarketBar.objects.filter(source=feed.instance_id,symbol=feed.symbol,timeframe='H1',
            timestamp__lte=now-timedelta(hours=1)).order_by('-timestamp')[:options['limit']])[::-1]
        raw=[candle(r) for r in rows]; bars=[b for b in raw if valid_bar(b,3600)]
        if not bars: raise CommandError('No valid completed H1 history')
        snapshot={'H1':bars}
        if options['snapshot_output']:
            Path(options['snapshot_output']).write_text(json.dumps(snapshot,sort_keys=True,allow_nan=False))
        try: commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True,stderr=subprocess.DEVNULL).strip()
        except (FileNotFoundError,subprocess.CalledProcessError): commit='UNAVAILABLE_IN_IMAGE; USE_POLICY_SHA256'
        report=dict(generated_at=now.isoformat(),source=feed.instance_id,symbol=feed.symbol,server=feed.server_name,
            git_commit=commit,policy_sha256=hashlib.sha256(Path(trend_shadow.__file__).read_bytes()).hexdigest(),
            input_sha256=hashlib.sha256(json.dumps(snapshot,sort_keys=True).encode()).hexdigest(),
            policy=asdict(TrendPolicy()),contract={'point':costs.point,'tick_size':tick},
            first_h1=bars[0]['time'],last_h1=bars[-1]['time'],
            data_quality={'raw_h1':len(raw),'valid_h1':len(bars),'excluded_invalid_or_off_grid':len(raw)-len(bars),
                'h1_gap_count':sum(b['time']-a['time']!=3600 for a,b in zip(bars,bars[1:])),
                'complete_derived_h4':len(aggregate_h4(bars))},
            main_thresholds=list(DecisionThresholdConfig.objects.values('profile','values','updated_at')),
            execution='NEXT_FULL_H1_AFTER_RECORDING; ONE_HOUR_DELAY_FROM_SIGNAL_CLOSE; STOP_FIRST; ASK_SHORTS',
            limitations=['Descriptive development history; not untouched out-of-sample',
                'Historical receipt availability and executable quotes not reconstructed',
                'Fixed costs; overnight debit at assumed UTC rollover with Wednesday triple, not broker swap',
                'Indicators use observed session bars; gaps not filled; recent breakout window must be consecutive',
                'Outcome gaps skipped, not bridged; H1 stop/target ambiguity resolves stop-first',
                'R results are hypothetical price-risk units; no lot sizing or broker minimum applied',
                'Closed cumulative-R drawdown, not intratrade or account drawdown',
                'Main/scalper have different execution horizons; no like-for-like ranking established',
                'Unchanged-price forecast and no-position baseline are zero price movement/zero trading P&L'])
        report['base']=replay_trend(bars,costs=costs,tick_size=tick)
        report['stress']=replay_trend(bars,costs=replace(costs,spread_points=costs.spread_points*2,
            slippage_points=costs.slippage_points*2,overnight_points=costs.overnight_points*2),tick_size=tick)
        self.stdout.write(json.dumps(report,indent=2,default=str,allow_nan=False))
