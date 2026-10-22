"""Local CPU XGBoost action-value reconstruction; no live integration."""
from __future__ import annotations
import argparse
import json
from pathlib import Path

from .historical_benchmark import _metrics
from .trade_selector_lab import SELECTOR_FEATURES, choose, replay

CONFIGS = (
    {'max_depth':2,'rounds':100},
    {'max_depth':4,'rounds':150},
    {'max_depth':6,'rounds':200},
)


class TreeValueModel:
    def __init__(self, booster):
        self.booster=booster
        self.cache={}

    def prefill(self, vectors):
        import numpy as np
        if vectors:
            values=self.booster.inplace_predict(np.asarray(vectors,dtype=np.float32))
            self.cache.update(zip(map(tuple,vectors),map(float,values)))

    def predict(self, vector):
        key=tuple(vector)
        if key not in self.cache:
            self.prefill([vector])
        return self.cache[key]


def fit_pair(samples, outcomes, config, *, cpu_workers=2, artifact_dir=None, metadata=None):
    import numpy as np
    import xgboost as xgb
    if cpu_workers<1 or len(samples)<100:
        raise ValueError('need positive CPU workers and >=100 training samples')
    matrix=np.asarray([x for i,x in samples],dtype=np.float32)
    if matrix.shape[1]!=len(SELECTOR_FEATURES) or not np.isfinite(matrix).all():
        raise ValueError('invalid tree feature matrix')
    params={'objective':'reg:squarederror','tree_method':'hist','device':'cpu',
            'nthread':cpu_workers,'seed':42,'eta':.05,'min_child_weight':20,
            'lambda':10.,'subsample':1.,'colsample_bytree':1.,
            'max_bin':128,'max_depth':config['max_depth']}
    models=[]
    for side in ('BUY','SELL'):
        targets=np.asarray([outcomes[i][side][0] for i,x in samples],dtype=np.float32)
        if not np.isfinite(targets).all():
            raise ValueError('invalid tree labels')
        dataset=xgb.DMatrix(matrix,label=targets,feature_names=list(SELECTOR_FEATURES),nthread=cpu_workers)
        booster=xgb.train(params,dataset,num_boost_round=config['rounds'])
        models.append(TreeValueModel(booster))
        if artifact_dir:
            artifact_dir.mkdir(parents=True,exist_ok=True)
            booster.save_model(artifact_dir/f'{side}.json')
    if artifact_dir:
        (artifact_dir/'metadata.json').write_text(json.dumps({
            'schema':'smc-xgboost-action-value-v1','feature_names':SELECTOR_FEATURES,
            'model_order':['BUY','SELL'],'config':config,'parameters':params,
            'xgboost_version':xgb.__version__,'numpy_version':np.__version__,
            'training_samples':len(samples),'metadata':metadata or {},
            'model_type':'regression of net trade R, not the remote direction classifier'},indent=2)+'\n')
    return tuple(models)


def select_inner(samples, outcomes, *, horizon, training_start, validation_start,
                 artifact_dir=None,cpu_workers=2):
    train=[(i,x) for i,x in samples if i+horizon<validation_start]
    valid=[(i,x) for i,x in samples if i>=validation_start]
    inner={'train_samples':len(train),'validation_samples':len(valid),
           'last_training_label_index':train[-1][0]+horizon if train else None,
           'validation_start_index':validation_start}
    if len(train)<100 or not valid:
        return None,[],dict(inner,reason='insufficient_inner_samples')
    candidates=[];best=None
    for config in CONFIGS:
        dest=artifact_dir/f"depth_{config['max_depth']}" if artifact_dir else None
        pair=fit_pair(train,outcomes,config,cpu_workers=cpu_workers,artifact_dir=dest,metadata=inner)
        for model in pair:
            model.prefill([x for i,x in valid])
        for threshold in (0.,.05,.1):
            trades,_=replay(valid,outcomes,lambda i,x:choose(pair,x,threshold))
            metrics=_metrics(trades)
            row={**config,'threshold_r':threshold,'metrics':metrics}
            candidates.append(row)
            if (metrics['trades']>=30 and metrics['profit_factor'] is not None
                    and metrics['profit_factor']>1 and metrics['mean_r']>0):
                if best is None or metrics['mean_r']>best['metrics']['mean_r']:
                    best=row
    return best,candidates,inner


def main():
    from .trade_selector_lab import run
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db',required=True)
    parser.add_argument('--symbol',default='XAUUSD_KAGGLE')
    parser.add_argument('--output-dir',default='data/xgboost_lab')
    parser.add_argument('--cpu-workers',type=int,default=2)
    parser.add_argument('--no-progress',action='store_true')
    args=parser.parse_args()
    if args.cpu_workers<1:
        parser.error('--cpu-workers must be positive')
    report=run(args.db,symbol=args.symbol,output_dir=args.output_dir,
               backend='xgboost',cpu_workers=args.cpu_workers,show_progress=not args.no_progress)
    print(json.dumps(report['gate'],indent=2))


if __name__=='__main__': main()
