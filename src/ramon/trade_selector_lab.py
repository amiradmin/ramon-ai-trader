"""Offline BUY/SELL/WAIT selection trained on cost-adjusted trade outcomes."""
from __future__ import annotations
import argparse
from collections import Counter
from dataclasses import asdict
import hashlib
import json
from math import isfinite
from pathlib import Path

from .core import atr14
from .history import load_bars
from .historical_benchmark import TradeRecord, _metrics, _window_iso, m15_gap_prefix, horizon_is_contiguous
from .smc_local_lab import features, fit, FEATURE_NAMES
from .progress import ProgressReporter

SELECTOR_FEATURES = FEATURE_NAMES + ("signal_spread_atr", "atr_pct")
SELECTOR_SCHEMA = "smc-trade-selector-v1"


def outcome(bars, spreads, i, direction, *, horizon=4, point=.01, fallback=42, cost=.1):
    """Same next-open, Bid/Ask, stop-first assumptions as historical benchmark."""
    scale = atr14(bars[max(0,i-255):i+1])
    stop_distance, target_distance = 1.5*scale, 3*scale
    if stop_distance <= point:
        raise ValueError('insufficient ATR')
    spread = lambda j: (spreads[j] if spreads[j]>0 else fallback)*point
    entry = bars[i+1].open + (spread(i+1) if direction=='BUY' else 0)
    stop = entry-stop_distance if direction=='BUY' else entry+stop_distance
    target = entry+target_distance if direction=='BUY' else entry-target_distance
    for j in range(i+1,i+horizon+1):
        b=bars[j]
        stop_hit = b.low<=stop if direction=='BUY' else b.high+spread(j)>=stop
        target_hit = b.high>=target if direction=='BUY' else b.low+spread(j)<=target
        if stop_hit or target_hit:
            return (-1-cost if stop_hit else 2-cost), j, ('LOSS' if stop_hit else 'WIN')
    delta = bars[i+horizon].close-entry if direction=='BUY' else entry-bars[i+horizon].close-spread(i+horizon)
    return delta/stop_distance-cost, i+horizon, 'TIMEOUT'


def choose(model_pair, vector, threshold):
    buy, sell = (model.predict(vector) for model in model_pair)
    if max(buy,sell) <= threshold:
        return 'WAIT'
    return 'BUY' if buy>=sell else 'SELL'


def move_side(move):
    return "BUY" if move>0 else ("SELL" if move<0 else "WAIT")


def replay(samples, outcomes, policy):
    trades=[]
    counts=Counter()
    next_free=0
    for i,vector in samples:
        if i<next_free:
            continue
        direction=policy(i,vector)
        counts[direction]+=1
        if direction=='WAIT':
            continue
        r,closed,exit_kind=outcomes[i][direction]
        trades.append(TradeRecord(i,direction,exit_kind,r,'RESEARCH'))
        next_free=closed+1
    return trades, dict(counts)


def select_inner(samples, outcomes, *, horizon, training_start, validation_start, artifact_dir=None):
    train=[(i,x) for i,x in samples if i+horizon<validation_start]
    valid=[(i,x) for i,x in samples if i>=validation_start]
    if len(train)<100 or not valid:
        return None, [], {'train_samples':len(train),'validation_samples':len(valid),
            'last_training_label_index':train[-1][0]+horizon if train else None,
            'validation_start_index':validation_start,'reason':'insufficient_inner_samples'}
    candidates=[]
    best=None
    for l2 in (.1,1.,10.):
        pair=tuple(fit([x for i,x in train],[outcomes[i][side][0] for i,x in train],
                       l2=l2,feature_names=SELECTOR_FEATURES,
                       metadata={'side':side,'horizon':horizon,'label':'net_trade_R','training_last_label':train[-1][0]+horizon})
                   for side in ('BUY','SELL'))
        if artifact_dir is not None:
            artifact_dir.mkdir(parents=True,exist_ok=True)
            (artifact_dir/f'l2_{l2:g}.json').write_text(json.dumps({
                'schema':SELECTOR_SCHEMA, 'models':[asdict(m) for m in pair], 'feature_names':SELECTOR_FEATURES,
                'l2':l2,'training_last_label_index':train[-1][0]+horizon,
                'validation_start_index':validation_start},indent=2,allow_nan=False)+'\n')
        for threshold in (0.,.05,.1):
            trades,_=replay(valid,outcomes,lambda i,x:choose(pair,x,threshold))
            metrics=_metrics(trades)
            row={'l2':l2,'threshold_r':threshold,'metrics':metrics}
            candidates.append(row)
            if (metrics['trades']>=30 and metrics['profit_factor'] is not None
                    and metrics['profit_factor']>1 and metrics['mean_r']>0):
                if best is None or metrics['mean_r']>best['metrics']['mean_r']:
                    best=row
    return best,candidates, {'train_samples':len(train),'validation_samples':len(valid),
        'last_training_label_index':train[-1][0]+horizon,
        'validation_start_index':validation_start}


def run(db, *, symbol='XAUUSD_KAGGLE', output_dir='data/trade_selector_lab',
        stride=4, train_stride=16, horizon=4, cost=.1, fallback=42, point=.01,
        show_progress=True):
    if (min(stride,train_stride,horizon,fallback)<=0 or cost<0 or point<=0
            or not all(isfinite(v) for v in (cost,point))):
        raise ValueError('invalid research configuration')
    bars,spreads=load_bars(db,symbol)
    gaps=m15_gap_prefix(bars)
    warmup=len(bars)//5
    if warmup<2000:
        raise ValueError('need >=10000 M15 bars for nested research')
    destination=Path(output_dir);destination.mkdir(parents=True,exist_ok=True)
    digest=hashlib.sha256()
    for b,spread in zip(bars,spreads):
        digest.update(json.dumps([asdict(b),spread],sort_keys=True).encode())
    fingerprint=digest.hexdigest()
    progress=ProgressReporter(10000,'Cost-aware trade selector',enabled=show_progress)
    cache={}; labels={}; pooled={}; report={'schema_version':1,'advisor':'SMC_TRADE_SELECTOR',
        'dataset':{'bars':len(bars),'symbol':symbol,'sha256':fingerprint,'gaps':gaps[-1],
                   'window':_window_iso(bars,0,len(bars))},
        'method':{'folds':5,'horizon':horizon,'evaluation_stride':stride,'training_stride':train_stride,
                  'point':point,'fallback_spread':fallback,'additional_cost_r':cost,
                  'stop_atr':1.5,'target_atr':3.,'feature_schema':SELECTOR_SCHEMA,
                  'feature_names':SELECTOR_FEATURES,'label':'hypothetical BUY and SELL net R, next-open entry',
                  'inner_validation':'last 25% of prior history; purge all label overlap',
                  'selection_grid':{'l2':[.1,1.,10.],'threshold_r':[0.,.05,.1]},
                  'inner_gate':'>=30 trades, PF>1 and MeanR>0; rank by MeanR',
                  'no_inner_candidate':'WAIT entire outer fold; never choose from held-out outcomes',
                  'gap_guard':'exclude horizon deltas !=900s; context may contain historical gaps',
                  'live_changes':False,
                  'implementation_sha256':{n:hashlib.sha256(Path(__file__).with_name(n).read_bytes()).hexdigest()
                    for n in ('trade_selector_lab.py','smc_local_lab.py','smc_features.py','historical_benchmark.py','core.py')}},
        'limitations':['External assumed spreads; no live EA management, tick fills or slippage.',
                       'Pure research decision policy does not run or weaken live entry filters.',
                       'Baselines below use the same fixed exits, not the legacy forecast entry filters.',
                       'Gap exclusions use future timestamps for offline eligibility only.',
                       'Drawdown is closed-trade R, not intratrade equity drawdown.',
                       'Archive reused in prior research; algorithmic OOS is not a pristine project-level holdout.'], 'folds':[]}
    def sample(i):
        if i not in cache:
            scale=atr14(bars[i-255:i+1])
            signal_spread=(spreads[i] if spreads[i]>0 else fallback)*point
            cache[i]=features(bars[i-255:i+1])+(signal_spread/scale,scale/bars[i].close)
            labels[i]={side:outcome(bars,spreads,i,side,horizon=horizon,point=point,fallback=fallback,cost=cost)
                       for side in ('BUY','SELL')}
        return (i,cache[i])
    for fold in range(5):
        start=warmup+(len(bars)-warmup)*fold//5
        end=warmup+(len(bars)-warmup)*(fold+1)//5
        training=[]
        for i in range(256,start-horizon,train_stride):
            if horizon_is_contiguous(gaps,i,horizon) and atr14(bars[i-255:i+1])>point:
                training.append(sample(i))
            progress.update(fold*2000+int(400*(i-256)/max(1,start-256)),stage=f'fold {fold+1}: labels/features')
        validation_start=256+(start-256)*3//4
        progress.update(fold*2000+400,stage=f'fold {fold+1}: inner selection',force=True)
        selected,candidates,inner=select_inner(training,labels,horizon=horizon,
                                               training_start=256,validation_start=validation_start,
                                               artifact_dir=destination/f'fold_{fold+1}_inner_models')
        pair=None
        if selected:
            pair=tuple(fit([x for i,x in training],[labels[i][side][0] for i,x in training],
                        l2=selected['l2'],feature_names=SELECTOR_FEATURES,metadata={'side':side,'label':'net_trade_R',
                        'last_training_label_index':training[-1][0]+horizon,'outer_start':start})
                        for side in ('BUY','SELL'))
            (destination/f'fold_{fold+1}_model.json').write_text(json.dumps(
                {'schema':SELECTOR_SCHEMA,'models':[asdict(m) for m in pair],'selected':selected,'features':SELECTOR_FEATURES},indent=2)+'\n')
        evaluation=[]
        for i in range(start,end-horizon,stride):
            if horizon_is_contiguous(gaps,i,horizon) and atr14(bars[i-255:i+1])>point:
                evaluation.append(sample(i))
            progress.update(fold*2000+800+int(1100*(i-start)/max(1,end-start)),stage=f'fold {fold+1}: held-out evaluation')
        policies={'SELECTOR':lambda i,x:choose(pair,x,selected['threshold_r']) if pair else 'WAIT',
                  'always_buy':lambda i,x:'BUY','always_sell':lambda i,x:'SELL',
                  'previous_bar':lambda i,x:move_side(bars[i].close-bars[i-1].close),
                  'contrarian_previous_bar':lambda i,x:move_side(bars[i-1].close-bars[i].close),
                  'momentum_4bar':lambda i,x:move_side(bars[i].close-bars[i-4].close),
                  'contrarian_momentum_4bar':lambda i,x:move_side(bars[i-4].close-bars[i].close)}
        row={'fold':fold+1,'evaluation':_window_iso(bars,start,end),
             'training_samples':len(training),'last_training_label_index':training[-1][0]+horizon,
             'inner':inner,'inner_candidates':candidates,'selected':selected,
             'inner_model_artifacts':str(destination/f'fold_{fold+1}_inner_models'),
             'rejection':None if selected else 'no_inner_candidate_meets_predeclared_gate','models':{}}
        for name,policy in policies.items():
            trades,counts=replay(evaluation,labels,policy)
            pooled.setdefault(name,[]).extend(trades)
            row['models'][name]={'metrics':_metrics(trades),'decisions':counts}
        report['folds'].append(row)
        (destination/'report.partial.json').write_text(json.dumps(report,indent=2)+'\n')
        progress.update((fold+1)*2000,stage=f'fold {fold+1}: complete',force=True)
    report['overall']={name:_metrics(trades) for name,trades in pooled.items()}
    metrics=[f['models']['SELECTOR']['metrics'] for f in report['folds']]
    eligible=all(m['trades']>=30 and m['profit_factor'] is not None and m['profit_factor']>1 and m['mean_r']>0 for m in metrics)
    beats=all(m['mean_r'] is not None and all(b['metrics']['mean_r'] is not None and m['mean_r']>b['metrics']['mean_r']
              for name,b in f['models'].items() if name!='SELECTOR') for f,m in zip(report['folds'],metrics))
    report['gate']={'eligible_for_broker_validation':eligible and beats,'live_promotion':False,'shadow_enabled':False}
    (destination/'report.json').write_text(json.dumps(report,indent=2,allow_nan=False)+'\n')
    progress.finish(stage='complete; research only')
    return report


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--db',required=True);p.add_argument('--symbol',default='XAUUSD_KAGGLE')
    p.add_argument('--output-dir',default='data/trade_selector_lab')
    p.add_argument('--no-progress',action='store_true')
    args=p.parse_args()
    r=run(args.db,symbol=args.symbol,output_dir=args.output_dir,show_progress=not args.no_progress)
    print(json.dumps(r['gate'],indent=2))

if __name__=='__main__': main()
