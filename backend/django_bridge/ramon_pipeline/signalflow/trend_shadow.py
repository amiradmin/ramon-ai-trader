"""Frozen H1/H4 research candidate. Pure functions; no broker dependencies."""
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import math

from .scalping import ScalpCosts, continuous, ema, valid_bar

VERSION = 'h1-h4-breakout-v1'


@dataclass(frozen=True)
class TrendPolicy:
    h1_window: int = 64
    h4_window: int = 64
    breakout_bars: int = 20
    stop_atr: float = 2.0
    reward_risk: float = 3.0
    max_holding_bars: int = 48
    maximum_cost_to_risk: float = .20


@dataclass(frozen=True)
class TrendCosts(ScalpCosts):
    # Conservative debit assumptions, not broker swap quotes; Wednesday is triple.
    overnight_points: float = 50.0
    rollover_utc_hour: int = 22

    def __post_init__(self):
        super().__post_init__()
        if (isinstance(self.overnight_points, bool) or not math.isfinite(self.overnight_points)
                or self.overnight_points < 0 or type(self.rollover_utc_hour) is not int
                or not 0 <= self.rollover_utc_hour <= 23):
            raise ValueError('Invalid explicit overnight cost assumption')


def aggregate_h4(h1):
    """UTC four-hour buckets. Omit incomplete buckets; never invent missing H1."""
    groups = {}
    for bar in h1:
        if valid_bar(bar, 3600):
            groups.setdefault(int(bar['time'])//14400*14400, []).append(bar)
    output = []
    for stamp, bars in sorted(groups.items()):
        bars = sorted(bars, key=lambda b: b['time'])
        if len(bars) == 4 and [b['time'] for b in bars] == [stamp+i*3600 for i in range(4)]:
            output.append(dict(time=stamp, open=bars[0]['open'], close=bars[-1]['close'],
                               high=max(b['high'] for b in bars), low=min(b['low'] for b in bars)))
    return output


def detect_trend(h1, *, bid, costs, tick_size, policy=TrendPolicy()):
    def wait(reason, **metrics):
        return dict(version=VERSION, action='WAIT', reason=reason, **metrics)
    if not all(math.isfinite(v) and v > 0 for v in (bid, tick_size)):
        return wait('INVALID_QUOTE_OR_TICK')
    if len(h1) < policy.h1_window:
        return wait('H1_WARMUP')
    if any(not valid_bar(b, 3600) for b in h1) or any(b['time'] <= a['time'] for a,b in zip(h1,h1[1:])):
        return wait('INVALID_OR_UNSORTED_H1')
    h4 = aggregate_h4(h1)[-policy.h4_window:]
    if len(h4) < policy.h4_window:
        return wait('H4_WARMUP', h4_available=len(h4), h4_required=policy.h4_window)
    cutoff = h1[-1]['time']+3600
    if not 0 <= cutoff-h4[-1]['time']-14400 < 14400:
        return wait('H4_STALE')
    # Indicators use observed bars across historical session gaps, without filling.
    # The breakout/ATR window itself must be consecutive to avoid gap artifacts.
    if not continuous(h1[-policy.breakout_bars-1:], 3600):
        return wait('RECENT_H1_GAP')
    close = [b['close'] for b in h1[-policy.h1_window:]]
    fast, slow = ema(close,20), ema(close,50)
    higher = [b['close'] for b in h4]
    hfast, hslow = ema(higher,20), ema(higher,50)
    sign = (1 if fast[-1]>slow[-1] and hfast[-1]>hslow[-1]>hslow[-2]
            else -1 if fast[-1]<slow[-1] and hfast[-1]<hslow[-1]<hslow[-2] else 0)
    if not sign:
        return wait('H1_H4_TREND_NOT_ALIGNED')
    prior = h1[-policy.breakout_bars-1:-1]
    if not (h1[-1]['close']>max(b['high'] for b in prior) if sign>0
            else h1[-1]['close']<min(b['low'] for b in prior)):
        return wait('AWAITING_H1_BREAKOUT')
    atr = sum(max(h1[i]['high']-h1[i]['low'],abs(h1[i]['high']-h1[i-1]['close']),
                  abs(h1[i]['low']-h1[i-1]['close'])) for i in range(len(h1)-14,len(h1)))/14
    entry = bid+(costs.spread_points*costs.point if sign>0 else 0)
    stop = entry-sign*policy.stop_atr*atr
    stop = (math.floor(stop/tick_size) if sign>0 else math.ceil(stop/tick_size))*tick_size
    risk = sign*(entry-stop)
    # Include worst-case four debit nights (48h plus triple rollover).
    if risk<=0 or (costs.total_price+4*costs.overnight_points*costs.point)/risk>policy.maximum_cost_to_risk:
        return wait('COST_TOO_HIGH_FOR_STOP')
    target = entry+sign*policy.reward_risk*risk
    target = (math.ceil(target/tick_size) if sign>0 else math.floor(target/tick_size))*tick_size
    if min(stop,target)<=0:
        return wait('INVALID_PLAN')
    return dict(version=VERSION,action='BUY' if sign>0 else 'SELL',reason='H1_BREAKOUT_H4_ALIGNED',
                entry_reference=entry,stop=stop,target=target,atr=atr,max_holding_bars=policy.max_holding_bars)


def overnight_charge(start, end, costs):
    charge = 0.0
    day = int(start)//86400*86400
    while day+costs.rollover_utc_hour*3600 <= end:
        rollover = day+costs.rollover_utc_hour*3600
        if start < rollover <= end:
            weight = 3 if datetime.fromtimestamp(rollover,timezone.utc).weekday()==2 else 1
            charge += weight*costs.overnight_points
        day += 86400
    return charge


def resolve_trend(bars, plan, costs, *, max_holding_bars=48):
    if not bars or not continuous(bars,3600) or plan.get('action') not in {'BUY','SELL'}:
        return {'result':'INVALID_DATA'}
    sign = 1 if plan['action']=='BUY' else -1
    spread,slip = costs.spread_points*costs.point,costs.slippage_points*costs.point
    entry = bars[0]['open']+(spread if sign>0 else 0)+sign*slip
    stop,target = plan['stop'],plan['target']
    risk,reward = sign*(entry-stop),sign*(target-entry)
    if min(risk,reward)<=0 or reward/risk<1 or (costs.total_price+4*costs.overnight_points*costs.point)/risk>.20:
        return {'result':'INVALID_AT_ENTRY'}
    for i,bar in enumerate(bars[:max_holding_bars]):
        opening,low,high = [bar[k]+(spread if sign<0 else 0) for k in ('open','low','high')]
        stop_hit = low<=stop if sign>0 else high>=stop
        target_hit = high>=target if sign>0 else low<=target
        if stop_hit:
            result,exit_price = 'STOP',(min(opening,stop) if sign>0 else max(opening,stop))-sign*slip
        elif target_hit:
            result,exit_price = 'TARGET',target-sign*slip
        elif i+1==max_holding_bars:
            result,exit_price = 'TIME',bar['close']+(spread if sign<0 else 0)-sign*slip
        else:
            continue
        # With H1 OHLC, charge through the end of exit candle conservatively.
        funding = overnight_charge(bars[0]['time'],bar['time']+3600,costs)
        net = sign*(exit_price-entry)/costs.point-costs.commission_points-funding
        return dict(result=result,entry=entry,exit=exit_price,net_points=net,net_r=net*costs.point/risk,
                    funding_points=funding,bars_held=i+1,exit_bar_time=bar['time'],
                    ambiguous_stop_target=bool(stop_hit and target_hit))
    return {'result':'OPEN'}


def replay_trend(h1, *, costs, tick_size):
    trades,reasons = [],Counter()
    i = TrendPolicy().h1_window-1
    while i<len(h1)-2:
        plan = detect_trend(h1[:i+1],bid=h1[i]['close'],costs=costs,tick_size=tick_size)
        if plan['action']=='WAIT':
            reasons[plan['reason']]+=1; i+=1; continue
        # Match forward collection: record just AFTER H1 closes; next full H1 open.
        future = h1[i+2:i+2+48]
        if not future or future[0]['time']!=h1[i]['time']+7200:
            reasons['ENTRY_GAP']+=1; i+=1; continue
        outcome = {'result':'OPEN'}
        for n in range(1,len(future)+1):
            outcome = resolve_trend(future[:n],plan,costs)
            if outcome['result']!='OPEN': break
        if outcome['result'] in {'STOP','TARGET','TIME'}:
            trades.append(dict(signal_time=h1[i]['time'],entry_time=future[0]['time'],plan=plan,**outcome))
            i+=outcome['bars_held']+2
        else:
            reasons[outcome['result']]+=1
            # Forward collector blocks overlaps until this attempt is settled/skipped.
            i+=len(future)+2 if outcome['result']=='OPEN' else n+2
    cumulative=peak=drawdown=0.0
    for t in trades:
        cumulative+=t['net_r']; peak=max(peak,cumulative); drawdown=max(drawdown,peak-cumulative)
    count=len(trades)
    return dict(version=VERSION,policy=asdict(TrendPolicy()),costs=asdict(costs),trade_count=count,
                trades=trades,rejections=dict(reasons),net_r=cumulative,
                expectancy_r=cumulative/count if count else None,
                win_rate=sum(t['net_points']>0 for t in trades)/count if count else None,
                net_points=sum(t['net_points'] for t in trades),closed_cumulative_r_drawdown=drawdown,
                no_trade_baseline_net_points=0,validation='DESCRIPTIVE_NOT_OUT_OF_SAMPLE')
