"""Offline expanding-window SMC_LOCAL research. Never imported by the live server."""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass, replace
from collections import Counter
import hashlib
import json
from math import isfinite, sqrt
from pathlib import Path
from statistics import mean, pstdev
from typing import Sequence

from .core import Bar, Forecast, Market, Settings, atr14
from .historical_benchmark import (
    PreviousBarBaseline, ContrarianBaseline, _metrics, _window_iso,
    m15_gap_prefix, horizon_is_contiguous, simulate,
)
from .compare import MomentumBaseline
from .history import load_bars, persist_shadow_vote
from .progress import ProgressReporter, ProgressSlice
from .smc_features import smc_technical_features

FEATURE_SCHEMA = 'smc-local-atr-v1'
PRICE_FEATURES = ('Open', 'High', 'Low', 'Close', 'SMA_20', 'SMA_50',
                  'EMA_12', 'EMA_26', 'BB_upper', 'BB_middle', 'BB_lower',
                  'Close_lag1', 'Close_lag2', 'Close_lag3')
DISTANCE_FEATURES = ('MACD', 'MACD_signal', 'MACD_hist', 'FVG_Size')
TYPE_FEATURES = ('FVG_Type', 'OB_Type', 'Recovery_Type')
FEATURE_NAMES = PRICE_FEATURES + DISTANCE_FEATURES + ('RSI',) + TYPE_FEATURES


def features(bars: Sequence[Bar]) -> tuple[float, ...]:
    """Use only completed bars; anchor price levels to current close/ATR."""
    raw = smc_technical_features(bars[-256:])
    anchor = bars[-1].close
    scale = max(atr14(bars), 1e-9)
    values = tuple((raw[k] - anchor) / scale for k in PRICE_FEATURES)
    values += tuple(raw[k] / scale for k in DISTANCE_FEATURES)
    values += ((raw['RSI'] - 50.0) / 50.0,)
    values += tuple(raw[k] for k in TYPE_FEATURES)
    if not all(isfinite(x) for x in values):
        raise ValueError('non-finite SMC feature')
    return values


def solve(matrix: list[list[float]], rhs: list[float]) -> tuple[float, ...]:
    """Small positive-definite ridge system with pivoted elimination."""
    a = [row[:] + [value] for row, value in zip(matrix, rhs)]
    n = len(a)
    for col in range(n):
        pivot = max(range(col, n), key=lambda i: abs(a[i][col]))
        a[col], a[pivot] = a[pivot], a[col]
        divisor = a[col][col]
        if abs(divisor) < 1e-12:
            raise ValueError('singular ridge fit')
        a[col] = [v / divisor for v in a[col]]
        for i in range(n):
            if i != col:
                factor = a[i][col]
                a[i] = [x - factor*y for x, y in zip(a[i], a[col])]
    return tuple(row[-1] for row in a)


@dataclass(frozen=True)
class RidgeModel:
    means: tuple[float, ...]
    scales: tuple[float, ...]
    weights: tuple[float, ...]
    bias: float
    residual_width: float
    metadata: dict

    def predict(self, values: Sequence[float]) -> float:
        if len(values) != len(self.weights) or not all(isfinite(v) for v in values):
            raise ValueError('invalid feature vector')
        return self.bias + sum(w*(v-m)/s for w, v, m, s in
                               zip(self.weights, values, self.means, self.scales))


def fit(rows: Sequence[Sequence[float]], targets: Sequence[float], *, l2: float,
        metadata: dict) -> RidgeModel:
    if len(rows) != len(targets) or len(rows) < 100 or l2 <= 0:
        raise ValueError('need >=100 aligned training rows and positive ridge penalty')
    if any(len(row) != len(FEATURE_NAMES) for row in rows):
        raise ValueError('invalid feature schema')
    if not all(isfinite(v) for row in rows for v in row) or not all(isfinite(v) for v in targets):
        raise ValueError('non-finite training data')
    means = tuple(mean(row[j] for row in rows) for j in range(len(FEATURE_NAMES)))
    scales = tuple(max(pstdev(row[j] for row in rows), 1e-9) for j in range(len(means)))
    bias = mean(targets)
    n, d = len(rows), len(means)
    gram = [[0.0]*d for _ in range(d)]
    rhs = [0.0]*d
    for row, target in zip(rows, targets):
        z = [(v-m)/s for v, m, s in zip(row, means, scales)]
        for j in range(d):
            rhs[j] += z[j]*(target-bias)
            for k in range(j+1):
                gram[j][k] += z[j]*z[k]
    for j in range(d):
        for k in range(j):
            gram[k][j] = gram[j][k]
        gram[j][j] += n*l2
    weights = solve(gram, rhs)
    provisional = RidgeModel(means, scales, weights, bias, 0.0, metadata)
    residuals = sorted(abs(y-provisional.predict(x)) for x, y in zip(rows, targets))
    # Training residual interval only; not a calibrated probability/confidence.
    width = max(residuals[min(len(residuals)-1, int(.8*len(residuals)))], 1e-6)
    return replace(provisional, residual_width=width)


@dataclass(frozen=True)
class FixedForecast:
    value: Forecast

    def forecast(self, closes, horizon):
        if horizon != len(self.value.median_path):
            raise ValueError('model horizon mismatch')
        return self.value


def bound_forecaster(model: RidgeModel, horizon: int):
    def bind(market: Market):
        last, scale = market.bars[-1].close, max(atr14(market.bars), market.point)
        move = model.predict(features(market.bars))*scale
        width = model.residual_width*scale
        median = max(market.point, last+move)
        path = tuple(max(market.point, last+move*(i+1)/horizon) for i in range(horizon))
        return FixedForecast(Forecast(max(market.point, median-width), median,
                                      median+width, path))
    return bind


def training_indices(bars, gaps, *, end: int, horizon: int, stride: int):
    # Strictly before evaluation start, including each future training label.
    return [i for i in range(256, end-horizon, stride)
            if horizon_is_contiguous(gaps, i, horizon)]


def promotion_gate(folds: Sequence[dict]) -> dict:
    metrics = [f['models']['SMC_LOCAL']['metrics'] for f in folds]
    adequate = all(m['trades'] >= 30 for m in metrics)
    stable = all(m['profit_factor'] is not None and m['profit_factor'] > 1
                 and m['mean_r'] > 0 for m in metrics)
    baseline = all(m['mean_r'] is not None and all(
        b['metrics']['mean_r'] is None or m['mean_r'] > b['metrics']['mean_r']
        for name, b in f['models'].items() if name != 'SMC_LOCAL')
        for f, m in zip(folds, metrics))
    return {'eligible_for_shadow': adequate and stable and baseline,
            'minimum_30_trades_each_fold': adequate,
            'positive_pf_and_mean_r_all_folds': stable,
            'mean_r_beats_all_baselines_each_fold': baseline,
            'live_promotion': False,
            'note': 'Fixed conservative research gate, not evidence of live profitability.'}


def run(db, *, symbol='XAUUSD_KAGGLE', folds=5, horizon=4, stride=4,
        train_stride=16, point=.01, fallback_spread=42, cost_r=.1, l2=1.0,
        output_dir='data/smc_local_lab', show_progress=True, shadow_db=None):
    if (folds != 5 or min(horizon, stride, train_stride) < 1 or cost_r < 0 or l2 <= 0
            or point <= 0 or fallback_spread <= 0
            or not all(isfinite(v) for v in (cost_r, l2, point))):
        raise ValueError('require five folds, positive strides/horizon/l2 and nonnegative cost')
    if shadow_db and Path(shadow_db).resolve() == Path(db).resolve():
        raise ValueError('shadow output must be separate from input database')
    if shadow_db and Path(shadow_db).exists():
        raise ValueError("use a new dedicated research shadow database")
    bars, spreads = load_bars(db, symbol)
    if len(bars) < 2000:
        raise ValueError('need at least 2000 M15 bars')
    gaps = m15_gap_prefix(bars)
    warmup = max(256, len(bars)//5)
    usable = len(bars)-warmup
    reporter = ProgressReporter(10000, 'SMC_LOCAL five-fold research', enabled=show_progress)
    settings = replace(Settings(), horizon=horizon, require_direction_confirmation=False,
                       market_state_policy_enabled=False)
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    # Input rows fingerprint, not filesystem metadata; consistent after copies.
    digest = hashlib.sha256()
    for b, spread in zip(bars, spreads):
        digest.update(json.dumps([asdict(b), spread], sort_keys=True).encode())
    fingerprint = digest.hexdigest()
    settings_hash = hashlib.sha256(json.dumps(asdict(settings), sort_keys=True).encode()).hexdigest()
    source_hashes = {name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
                     for name in ('smc_local_lab.py', 'smc_features.py', 'core.py',
                                  'historical_benchmark.py', 'compare.py')}

    report = {'schema_version': 1, 'advisor': 'SMC_LOCAL',
        'dataset': {'db': str(db), 'symbol': symbol, 'bars': len(bars),
                    'timestamp_gaps': gaps[-1], 'fingerprint_sha256': fingerprint,
                    'window': _window_iso(bars, 0, len(bars)),
                    'known_spread_bars': sum(s > 0 for s in spreads)},
        'method': {'folds': folds, 'horizon': horizon, 'evaluation_stride': stride,
                   'training_stride': train_stride, 'l2': l2, 'cost_r': cost_r,
                   'fallback_spread_points': fallback_spread, 'point': point,
                   'training': 'expanding past-only, label_end < evaluation_start',
                   'feature_schema': FEATURE_SCHEMA, 'features': FEATURE_NAMES,
                   'scaling': 'training fold only; price levels anchored to current close/ATR',
                   'target': 'future close minus signal close divided by signal ATR',
                   'uncertainty': '80th percentile absolute training residual, uncalibrated',
                   'gap_guard': 'future horizon deltas must be 900s; prior context may contain gaps',
                   'settings': asdict(settings), 'settings_sha256': settings_hash,
                   'positions': 'non-overlapping within each independent fold',
                   'hyperparameter_search': False, 'live_changes': False,
                   'implementation_sha256': source_hashes},
        'limitations': ['External OHLC and assumed spreads do not reproduce broker execution.',
                        'Stop-first M15 simulator; no tick fills, slippage or live EA exit management.',
                        'Future timestamps used only for offline eligibility, not live entry filtering.',
                        'Closed-trade drawdown only; excludes intratrade equity drawdown.',
                        'Order block and recovery features are local price proxies, not vendor equivalents.'],
        'folds': []}
    pooled_trades = {}
    rows_cache = {}
    for fold in range(folds):
        start = warmup + usable*fold//folds
        end = warmup + usable*(fold+1)//folds
        lo, hi = fold*2000, (fold+1)*2000
        indices = training_indices(bars, gaps, end=start, horizon=horizon, stride=train_stride)
        rows, targets = [], []
        for pos, i in enumerate(indices):
            if i not in rows_cache:
                rows_cache[i] = features(bars[i-255:i+1])
            rows.append(rows_cache[i])
            targets.append((bars[i+horizon].close-bars[i].close)/max(atr14(bars[i-255:i+1]), point))
            reporter.update(lo + int(400*(pos+1)/max(1,len(indices))), stage=f'fold {fold+1}: training features')
        reporter.update(lo+400, stage=f'fold {fold+1}: ridge fit', force=True)
        metadata = {'feature_schema': FEATURE_SCHEMA, 'horizon': horizon, 'l2': l2,
                    'training_samples': len(indices), 'training_last_signal_index': indices[-1] if indices else None,
                    'training_last_label_index': indices[-1]+horizon if indices else None,
                    'evaluation_start_index': start, 'dataset_sha256': fingerprint}
        model = fit(rows, targets, l2=l2, metadata=metadata)
        model_path = destination / f'fold_{fold+1}_model.json'
        model_path.write_text(json.dumps({'model': asdict(model), 'feature_names': FEATURE_NAMES,
                                          'schema': FEATURE_SCHEMA}, indent=2, allow_nan=False)+'\n')
        baselines = [('SMC_LOCAL', None), ('previous_bar', PreviousBarBaseline()),
                     ('momentum_4bar', MomentumBaseline()),
                     ('contrarian_previous_bar', ContrarianBaseline(PreviousBarBaseline())),
                     ('contrarian_momentum_4bar', ContrarianBaseline(MomentumBaseline()))]
        fold_report = {'fold': fold+1, 'evaluation': _window_iso(bars,start,end),
                       'training': metadata, 'model_artifact': str(model_path), 'models': {}}
        future_closes = {bars[i].time: bars[i+horizon].close for i in range(start,end-horizon)}
        for pos, (name, baseline) in enumerate(baselines):
            reasons = Counter()
            direction = Counter()
            errors = []

            def observe(market, decision):
                reasons.update([decision.reason])
                if decision.forecast_median <= 0:
                    return
                predicted = decision.forecast_median - market.bid
                actual = future_closes[market.bars[-1].time] - market.bid
                errors.append(((predicted-actual)/max(decision.atr, point))**2)
                if predicted == 0 or actual == 0:
                    direction['flat'] += 1
                elif (predicted > 0) == (actual > 0):
                    direction['correct'] += 1
                else:
                    direction['wrong'] += 1

            trades, counters = simulate(bars, spreads, baseline, symbol=symbol, point=point,
                settings=settings, start=start, end=end, stride=stride,
                fallback_spread_points=fallback_spread, roundtrip_cost_r=cost_r,
                market_forecaster=bound_forecaster(model, horizon) if baseline is None else None,
                decision_observer=observe,
                progress=ProgressSlice(reporter,lo+500+pos*300,lo+800+pos*300,end-start,name),
                progress_stage=f'fold {fold+1}: {name}')
            pooled_trades.setdefault(name, []).extend(trades)
            fold_report['models'][name] = {'metrics': _metrics(trades), **counters,
                                         'decision_reasons': dict(reasons),
                                         'forecast_diagnostics': {
                                             **dict(direction),
                                             'samples': direction['correct']+direction['wrong'],
                                             'accuracy': (direction['correct']/(direction['correct']+direction['wrong'])
                                                          if direction['correct']+direction['wrong'] else None),
                                             'rmse_atr': sqrt(mean(errors)) if errors else None,
                                             'sample_policy': 'evaluated decisions with forecast; skips bars while a trade is open'}}
        report['folds'].append(fold_report)
        (destination/'report.partial.json').write_text(json.dumps(report,indent=2,allow_nan=False)+'\n')
    report['overall'] = {name: _metrics(trades) for name, trades in pooled_trades.items()}
    report['gate'] = promotion_gate(report['folds'])
    report['shadow'] = {'requested': bool(shadow_db), 'votes_written': 0,
                        'mode': 'historical OOS research votes, not live integration'}
    if shadow_db and report['gate']['eligible_for_shadow']:
        # Re-evaluate held-out bars with their own frozen fold models. No orders.
        for f in report['folds']:
            raw = json.loads(Path(f['model_artifact']).read_text())['model']
            model = RidgeModel(**raw)
            bind = bound_forecaster(model,horizon)
            from .core import evaluate
            from .market_state import assess_market
            for i in range(f['evaluation']['start_index'],f['evaluation']['end_index_exclusive']-horizon,stride):
                if not horizon_is_contiguous(gaps,i,horizon):
                    continue
                spread = (spreads[i] if spreads[i]>0 else fallback_spread)*point
                market = Market(symbol,'M15',bars[i].close,bars[i].close+spread,point,bars[i-255:i+1])
                decision = evaluate(market,bind(market),settings)
                key = hashlib.sha256(f'{fingerprint}:{settings_hash}:{l2}:{f["fold"]}:{i}'.encode()).hexdigest()
                report['shadow']['votes_written'] += persist_shadow_vote(shadow_db,
                    vote_key='smc-local:'+key, captured=bars[i].time, symbol=symbol,
                    signal_bar_time=bars[i].time, advisor='SMC_LOCAL',decision=decision.decision,
                    regime=assess_market(market)['state'], metadata={'fold':f['fold'],'historical':True,
                    'feature_schema':FEATURE_SCHEMA,'model_artifact':f['model_artifact']},source_version=FEATURE_SCHEMA)
    (destination/'report.json').write_text(json.dumps(report,indent=2,allow_nan=False)+'\n')
    reporter.finish(stage='complete; research only')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db',required=True)
    parser.add_argument('--symbol',default='XAUUSD_KAGGLE')
    parser.add_argument('--horizon',type=int,default=4)
    parser.add_argument('--stride',type=int,default=4)
    parser.add_argument('--train-stride',type=int,default=16)
    parser.add_argument('--cost-r',type=float,default=.1)
    parser.add_argument('--fallback-spread',type=int,default=42)
    parser.add_argument('--point',type=float,default=.01)
    parser.add_argument('--output-dir',default='data/smc_local_lab')
    parser.add_argument('--shadow-db')
    parser.add_argument('--no-progress',action='store_true')
    args = parser.parse_args()
    report = run(args.db,symbol=args.symbol,horizon=args.horizon,stride=args.stride,
        train_stride=args.train_stride,cost_r=args.cost_r,fallback_spread=args.fallback_spread,
        point=args.point,output_dir=args.output_dir,shadow_db=args.shadow_db,
        show_progress=not args.no_progress)
    print(json.dumps(report['gate'],indent=2))


if __name__ == '__main__':
    main()
