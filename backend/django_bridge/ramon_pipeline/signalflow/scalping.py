"""Frozen M1/M5 paper scalper. No broker, settings or command dependencies."""
from dataclasses import asdict, dataclass
import math
from collections import Counter

VERSION = "m1-m5-pullback-v1"


@dataclass(frozen=True)
class ScalpConfig:
    m1_window: int = 64
    m5_window: int = 32
    max_holding_bars: int = 5
    reward_risk: float = 1.25
    minimum_stop_atr: float = 0.5
    maximum_stop_atr: float = 2.0
    maximum_cost_to_risk: float = 0.20
    stop_buffer_atr: float = 0.10


@dataclass(frozen=True)
class ScalpCosts:
    point: float
    spread_points: float
    slippage_points: float
    commission_points: float  # round trip, per lot, expressed in symbol points

    def __post_init__(self):
        values = (self.point, self.spread_points, self.slippage_points, self.commission_points)
        if any(isinstance(v, bool) or not math.isfinite(v) for v in values) or self.point <= 0 or min(values[1:]) < 0:
            raise ValueError("Finite positive point and nonnegative explicit execution costs required")

    @property
    def total_price(self):
        return self.point*(self.spread_points+2*self.slippage_points+self.commission_points)


def valid_bar(bar, interval):
    try:
        values = [bar[k] for k in ("time", "open", "high", "low", "close")]
        return (all(not isinstance(v, bool) and math.isfinite(v) and v > 0 for v in values)
                and bar['time'] % interval == 0
                and bar['low'] <= min(bar['open'], bar['close']) <= max(bar['open'], bar['close']) <= bar['high'])
    except (KeyError, TypeError, ValueError):
        return False


def continuous(bars, interval):
    return all(valid_bar(b, interval) for b in bars) and all(
        b['time']-a['time'] == interval for a, b in zip(bars, bars[1:]))


def ema(values, period):
    result = [values[0]]
    for value in values[1:]:
        result.append(result[-1]+2/(period+1)*(value-result[-1]))
    return result


def detect_scalp(m1, m5, *, bid, costs, tick_size, stop_level_points=0, config=ScalpConfig()):
    """Call only with completed bars known at the decision time. No future prices."""
    def wait(reason, **metrics):
        return dict(version=VERSION, action="WAIT", reason=reason, **metrics)
    if not all(math.isfinite(v) for v in (bid, tick_size, stop_level_points)) or min(bid, tick_size) <= 0 or stop_level_points < 0:
        return wait("INVALID_CONTRACT_OR_QUOTE")
    if len(m1) < config.m1_window:
        return wait("M1_WARMUP")
    m1 = m1[-config.m1_window:]
    if not continuous(m1, 60):
        return wait("M1_GAP_OR_INVALID")
    cutoff = m1[-1]['time']+60
    # Discard incomplete/future M5 bars rather than allowing them into indicators.
    m5 = [b for b in m5 if b['time']+300 <= cutoff][-config.m5_window:]
    if len(m5) < config.m5_window:
        return wait("M5_WARMUP")
    if not continuous(m5, 300) or not 0 <= cutoff-m5[-1]['time']-300 < 300:
        return wait("M5_STALE_GAPPED_OR_INVALID")
    close = [b['close'] for b in m1]
    fast, slow = ema(close, 9), ema(close, 21)
    higher = [b['close'] for b in m5]
    hfast, hslow = ema(higher, 9), ema(higher, 21)
    atr = sum(max(m1[i]['high']-m1[i]['low'], abs(m1[i]['high']-m1[i-1]['close']),
        abs(m1[i]['low']-m1[i-1]['close'])) for i in range(len(m1)-14, len(m1)))/14
    if atr <= 0:
        return wait("ZERO_ATR")
    sign = (1 if fast[-1] > slow[-1] and hfast[-1] > hslow[-1] and hslow[-1] > hslow[-2]
            else -1 if fast[-1] < slow[-1] and hfast[-1] < hslow[-1] and hslow[-1] < hslow[-2] else 0)
    if not sign:
        return wait("M1_M5_TREND_NOT_ALIGNED")
    prior = m1[-4:-1]
    pullback = any(b['low'] <= fast[i]+.15*atr if sign > 0 else b['high'] >= fast[i]-.15*atr
                   for i, b in [(len(m1)-3, m1[-3]), (len(m1)-2, m1[-2])])
    breaks = (close[-1] > max(b['high'] for b in prior) and close[-1] > m1[-1]['open'] if sign > 0
              else close[-1] < min(b['low'] for b in prior) and close[-1] < m1[-1]['open'])
    if not pullback or not breaks:
        return wait("AWAITING_M1_PULLBACK_RESUMPTION", atr=atr)
    entry = bid+(costs.spread_points*costs.point if sign > 0 else 0)
    stop = (min(b['low'] for b in m1[-3:])-config.stop_buffer_atr*atr if sign > 0
            else max(b['high'] for b in m1[-3:])+config.stop_buffer_atr*atr)
    stop = (math.floor(stop/tick_size) if sign > 0 else math.ceil(stop/tick_size))*tick_size
    risk = sign*(entry-stop)
    if not config.minimum_stop_atr*atr <= risk <= config.maximum_stop_atr*atr:
        return wait("STOP_DISTANCE_OUTSIDE_ATR_LIMITS", atr=atr)
    if costs.total_price/risk > config.maximum_cost_to_risk:
        return wait("COST_TOO_HIGH_FOR_STOP", cost_to_risk=costs.total_price/risk, atr=atr)
    target = entry+sign*config.reward_risk*risk
    target = (math.ceil(target/tick_size) if sign > 0 else math.floor(target/tick_size))*tick_size
    exit_quote = bid if sign > 0 else bid+costs.spread_points*costs.point
    if min(sign*(exit_quote-stop), sign*(target-exit_quote)) < stop_level_points*costs.point:
        return wait("BROKER_STOP_DISTANCE")
    if min(stop, target) <= 0:
        return wait("INVALID_PLAN")
    return dict(version=VERSION, action="BUY" if sign > 0 else "SELL", reason="M1_PULLBACK_M5_ALIGNED",
        entry_reference=entry, stop=stop, target=target, atr=atr, cost_to_risk=costs.total_price/risk,
        max_holding_bars=config.max_holding_bars)


def resolve_scalp(bars, plan, costs, *, max_holding_bars=5):
    """Exact consecutive bid M1 OHLC; ask-side shorts, adverse slips, stop first."""
    if not bars or not continuous(bars, 60) or plan.get('action') not in {'BUY', 'SELL'}:
        return {'result': 'INVALID_DATA'}
    sign = 1 if plan['action'] == 'BUY' else -1
    spread, slip = costs.spread_points*costs.point, costs.slippage_points*costs.point
    entry = bars[0]['open']+(spread if sign > 0 else 0)+sign*slip
    stop, target = plan['stop'], plan['target']
    risk, reward = sign*(entry-stop), sign*(target-entry)
    if min(risk, reward) <= 0 or reward/risk < 1.0 or costs.total_price/risk > .20:
        return {'result': 'INVALID_AT_ENTRY'}
    for i, bar in enumerate(bars[:max_holding_bars]):
        opening, low, high = [bar[k]+(spread if sign < 0 else 0) for k in ('open','low','high')]
        stop_hit = low <= stop if sign > 0 else high >= stop
        target_hit = high >= target if sign > 0 else low <= target
        result = None
        if stop_hit:
            result, exit_price = 'STOP', (min(opening, stop) if sign > 0 else max(opening, stop))-sign*slip
        elif target_hit:
            result, exit_price = 'TARGET', target-sign*slip
        elif i+1 == max_holding_bars:
            result, exit_price = 'TIME', bar['close']+(spread if sign < 0 else 0)-sign*slip
        if result:
            net = sign*(exit_price-entry)-costs.commission_points*costs.point
            return dict(result=result, entry=entry, exit=exit_price, net_points=net/costs.point,
                net_r=net/risk, bars_held=i+1, exit_bar_time=bar['time'],
                ambiguous_stop_target=bool(stop_hit and target_hit))
    return {'result': 'OPEN'}


def replay_scalper(m1, m5, *, costs, tick_size, stop_level_points=0, score_start=0, config=ScalpConfig()):
    trades, reasons = [], Counter()
    i, decisions = config.m1_window-1, 0
    while i < len(m1)-1:
        if m1[i]['time']+60 < score_start:
            i += 1
            continue
        plan = detect_scalp(m1[:i+1], m5, bid=m1[i]['close'], costs=costs,
                            tick_size=tick_size, stop_level_points=stop_level_points, config=config)
        decisions += 1
        if plan['action'] == 'WAIT':
            reasons[plan['reason']] += 1
            i += 1
            continue
        future = m1[i+1:i+1+config.max_holding_bars]
        if not future or future[0]['time'] != m1[i]['time']+60 or not continuous(future, 60):
            reasons['OUTCOME_GAP'] += 1
            i += 1
            continue
        outcome = resolve_scalp(future, plan, costs, max_holding_bars=config.max_holding_bars)
        if outcome['result'] in {'OPEN','INVALID_DATA','INVALID_AT_ENTRY'}:
            reasons[outcome['result']] += 1
            i += 1
            continue
        trades.append(dict(signal_time=m1[i]['time'], entry_time=future[0]['time'], plan=plan, **outcome))
        i += outcome['bars_held']+1  # no overlapping positions; next decision after exit bar
    cumulative = peak = drawdown = 0.0
    for trade in trades:
        cumulative += trade['net_r']; peak = max(peak, cumulative); drawdown = max(drawdown, peak-cumulative)
    n = len(trades)
    return dict(version=VERSION, policy=asdict(config), costs=asdict(costs), trades=trades,
        decisions=decisions, rejections=dict(reasons), trade_count=n, net_r=cumulative,
        expectancy_r=cumulative/n if n else None, win_rate=sum(t['net_r'] > 0 for t in trades)/n if n else None,
        net_points=sum(t['net_points'] for t in trades), closed_cumulative_r_drawdown=drawdown,
        no_trade_baseline_net_points=0, validation='DESCRIPTIVE_REPLAY_NOT_OUT_OF_SAMPLE',
        execution='NEXT_M1_OPEN_STOP_FIRST_ASK_SHORTS_ADVERSE_SLIPPAGE_BOTH_SIDES_COMMISSION')
