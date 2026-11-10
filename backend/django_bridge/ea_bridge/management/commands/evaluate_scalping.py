"""Read-only descriptive M1/M5 scalper replay; no optimizer or order writes."""
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from ea_bridge.models import EAHeartbeat, MarketBar, DecisionThresholdConfig
from ramon_pipeline.signalflow.scalping import ScalpConfig, ScalpCosts, replay_scalper, valid_bar
from ea_bridge.scalp_observer import candle


class Command(BaseCommand):
    help = 'Replay a frozen M1/M5 paper scalper with explicit costs. Never create orders or enable trading.'

    def add_arguments(self, parser):
        parser.add_argument('--source', required=True)
        parser.add_argument('--symbol', default='XAUUSD_o')
        parser.add_argument('--limit', type=int, default=10000)
        parser.add_argument('--snapshot-output', help='Optional local research snapshot; no database writes.')
        parser.add_argument('--spread-points', type=float, required=True)
        parser.add_argument('--slippage-points', type=float, required=True)
        parser.add_argument('--commission-points', type=float, required=True, help='Round trip, in symbol points.')

    def handle(self, *args, **options):
        if not 65 <= options['limit'] <= 20000:
            raise CommandError('limit must be 65–20000')
        feed = EAHeartbeat.objects.filter(instance_id=options['source'], symbol=options['symbol']).first()
        if not feed or type(feed.payload.get('account_trade_mode')) is not int or feed.payload['account_trade_mode'] != 0:
            raise CommandError('Matching explicit demo feed required')
        now = timezone.now()
        try:
            costs = ScalpCosts(float(feed.payload['quote']['point']), options['spread_points'],
                               options['slippage_points'], options['commission_points'])
            tick = float(feed.payload['symbol_info']['tick_size'])
            stop_level = float(feed.payload['symbol_info']['stops_level_points'])
        except (KeyError, TypeError, ValueError) as exc:
            raise CommandError(str(exc)) from exc
        data, quality = {}, {}
        for tf, interval in [('M1',60), ('M5',300)]:
            rows = list(MarketBar.objects.filter(source=options['source'], symbol=options['symbol'], timeframe=tf,
                timestamp__lte=now-timedelta(seconds=interval)).order_by('-timestamp')[:options['limit']])[::-1]
            raw = [candle(r) for r in rows]
            data[tf] = [r for r in raw if valid_bar(r, interval)]
            quality[tf] = dict(input_rows=len(raw), excluded_off_grid_or_invalid=len(raw)-len(data[tf]),
                gap_count=sum(b['time']-a['time'] != interval for a,b in zip(data[tf],data[tf][1:])))
        if len(data['M1']) < 65 or len(data['M5']) < 32:
            raise CommandError('Insufficient completed aligned M1/M5 history')
        report = dict(generated_at=now.isoformat(), source=options['source'], symbol=options['symbol'],
            server=feed.server_name, policy=asdict(ScalpConfig()), contract={'tick_size':tick,'stop_level_points':stop_level},
            data_quality=quality, first_m1=data['M1'][0]['time'], last_m1=data['M1'][-1]['time'],
            input_sha256=hashlib.sha256(json.dumps(data,sort_keys=True).encode()).hexdigest(),
            unchanged_strategy_thresholds=list(DecisionThresholdConfig.objects.values('profile','values','updated_at')),
            validation='DESCRIPTIVE_REPLAY; HISTORICAL_RECEIPT_AVAILABILITY_AND_QUOTES_NOT_RECONSTRUCTED',
            limitations=['Fixed spread and slippage assumptions, not historical executable quotes',
                'Bid OHLC approximation; no tick-path reconstruction', 'No swap/funding modeled',
                'Closed cumulative-R drawdown, not intratrade or account percentage drawdown',
                'M15 strategy is a different policy and is not represented by this scalper replay',
                'No-trade baseline is zero points; no claim that this beats the current M15 strategy'])
        if options.get('snapshot_output'):
            Path(options['snapshot_output']).write_text(json.dumps(data,sort_keys=True,allow_nan=False))
        for label, scenario in [('base',costs), ('stress',ScalpCosts(costs.point, costs.spread_points*2,
                costs.slippage_points*2, costs.commission_points))]:
            report[label] = replay_scalper(data['M1'], data['M5'], costs=scenario, tick_size=tick, stop_level_points=stop_level)
        self.stdout.write(json.dumps(report, indent=2, default=str, allow_nan=False))
