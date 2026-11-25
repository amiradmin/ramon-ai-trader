"""Offline chronological scalp experiments. No database or execution dependencies."""
from dataclasses import asdict, replace
from itertools import product
from .scalping import ScalpConfig, ScalpCosts, replay_scalper

SESSIONS = {'ALL': None, 'ASIA_UTC': tuple(range(0,8)),
            'LONDON_UTC': tuple(range(8,16)), 'NEW_YORK_UTC': tuple(range(13,21))}


def optimize(m1, m5, *, costs, tick_size, stop_level_points=0, minimum_trades=30):
    if len(m1) < 1000 or len(m5) < 32 or minimum_trades < 20:
        raise ValueError('Need at least 1000 M1 bars, 32 M5 bars and 20 minimum trades')
    if any(a['time'] >= b['time'] for bars in (m1,m5) for a,b in zip(bars,bars[1:])):
        raise ValueError('Duplicate or unordered candles')
    split = m1[int(len(m1)*.7)]['time']
    train = [b for b in m1 if b['time'] < split]
    stress = ScalpCosts(costs.point, costs.spread_points*2, costs.slippage_points*2, costs.commission_points)
    baseline = ScalpConfig()
    def run(cfg, side, session, sample, scenario, start=0):
        return replay_scalper(sample, m5, costs=scenario, tick_size=tick_size,
            stop_level_points=stop_level_points, score_start=start, config=cfg,
            direction=side, utc_hours=SESSIONS[session])
    def summary(result):
        return {k:v for k,v in result.items() if k != 'trades'}
    # Bounded one-factor search plus directional/holding/target combinations.
    candidates = [(baseline,'BOTH','ALL')]
    for side, holding, rr in product(('BOTH','BUY','SELL'), (1,3,5,10), (1.0,1.25,1.5,2.0)):
        candidates.append((replace(baseline,max_holding_bars=holding,reward_risk=rr),side,'ALL'))
    for stop, cost in product((1.0,1.5,2.0),(.10,.15,.20)):
        candidates.append((replace(baseline,maximum_stop_atr=stop,maximum_cost_to_risk=cost),'BOTH','ALL'))
    for session in SESSIONS:
        candidates.append((baseline,'BOTH',session))
    candidates = list(dict.fromkeys(candidates))
    rows = []
    for cfg, side, session in candidates:
        a = run(cfg,side,session,train,costs)
        b = run(cfg,side,session,train,stress)
        eligible = all(x['trade_count'] >= minimum_trades and x['net_r'] > 0 for x in (a,b))
        rows.append(dict(config=asdict(cfg),direction=side,session=session,
            training_base=summary(a),training_stress=summary(b),eligible=eligible,
            score=min(a['expectancy_r'],b['expectancy_r']) if eligible else None))
    ranked = sorted((row for row in rows if row['eligible']),key=lambda row:row['score'],reverse=True)
    # Lock one candidate before reading holdout outcomes. Baseline is a comparator only.
    selected = ranked[0] if ranked else None
    def validate(row):
        cfg=ScalpConfig(**row['config'])
        return {label:run(cfg,row['direction'],row['session'],m1,c,split+60)
                for label,c in (('base',costs),('stress',stress))}
    holdout = validate(selected) if selected else None
    baseline_holdout = validate(dict(config=asdict(baseline),direction='BOTH',session='ALL'))
    passed = bool(holdout and all(x['trade_count'] >= minimum_trades and x['net_r'] > 0
                                  for x in holdout.values()))
    return dict(mode='OFFLINE_RESEARCH_ONLY',split_time=split,training_bars=len(train),
        holdout_bars=len(m1)-len(train),minimum_trades_per_cost_case=minimum_trades,
        candidates_tested=len(rows),training_results=rows,selected=selected,
        holdout=holdout,baseline_holdout=baseline_holdout,
        status='DESCRIPTIVE_HOLDOUT_PASS' if passed else 'NO_VALIDATED_CANDIDATE',
        limitations=['Single chronological split; repeated use of holdout invalidates independence.',
            'Fixed UTC session windows, not DST-adjusted exchange sessions.',
            'Fixed costs and bid OHLC; no margin, live gate or account-risk simulation in ranking.',
            'No automatic live promotion; research-only parameters may exceed live bounds.'])
