"""Frozen common-input benchmark for pretrained forecasting models, no trading."""
from __future__ import annotations
import argparse
import hashlib
import fcntl
import json
import math
from pathlib import Path
import time

MODELS = {
 'chronos-small': 'autogluon/chronos-2-small',
 'chronos-base': 'amazon/chronos-2',
 'kronos-small': 'NeoQuasar/Kronos-small',
 'kronos-base': 'NeoQuasar/Kronos-base',
 'timesfm-2.5': 'google/timesfm-2.5-200m-pytorch',
 'timesfm-3': 'google/timesfm-3.0-pytorch',
 'fintext-tiny': 'FinText/Chronos_Tiny_2023_Global',
 'fintext-small': 'FinText/Chronos_Small_2023_Global',
}


def prepare(db, output, *, symbol='XAUUSD_l', count=120, lookback=256):
    from .core import atr14
    from .history import load_bars
    from .kronos_direction_shadow import acceptable_gap
    from .moving_average_lab import features, BASE
    bars,spreads=load_bars(db,symbol)
    candidates=[]
    for i in range(max(lookback-1,63),len(bars)-5):
        context=bars[i-lookback+1:i+1]
        if any(not acceptable_gap(a.time,b.time) for a,b in zip(context,context[1:])):
            continue
        if any(b.time-a.time!=900 for a,b in zip(bars[i:i+5],bars[i+1:i+6])):
            continue
        try: features(bars[i-63:i+1])
        except ValueError:continue
        if not candidates or i-candidates[-1]>=5:candidates.append(i)
    if len(candidates)<count or count<30:raise ValueError(f'need >=requested disjoint samples, got {len(candidates)}')
    chosen=[candidates[round(j*(len(candidates)-1)/(count-1))] for j in range(count)]
    rows=[]
    for i in chosen:
        scale=atr14(bars[i-63:i+1]);cost=(spreads[i] if spreads[i]>0 else 42)*.01+.1
        rows.append({'time':bars[i].time+900,'reference':bars[i].close,'atr':scale,
            'band':max(.2*scale,cost),'context':[[b.time,b.open,b.high,b.low,b.close] for b in bars[i-lookback+1:i+1]],
            'features':{n:features(bars[i-63:i+1])[n] for n in BASE},
            'future_times':[b.time for b in bars[i+1:i+6]],
            'actual':[b.close for b in bars[i+1:i+6]],'entry_bid':bars[i+1].open,
            'entry_spread':(spreads[i+1] if spreads[i+1]>0 else 42)*.01,
            'exit_spreads':[(spreads[j] if spreads[j]>0 else 42)*.01 for j in range(i+1,i+6)]})
    report={'schema':'foundation-benchmark-v1','symbol':symbol,'lookback':lookback,'samples':rows,
            'friction':.1,'point':.01,'fallback_spread_points':42,
            'selection':'uniform across valid broker contexts; >=5 bars between samples',
            'scope':'exploratory comparison; historical broker data already used in earlier research, not fresh holdout',
            'limitations':['Context permits normal daily/weekend closures; targets never cross gaps.',
                           'Unknown foundation pretraining overlap; no claim of independent unseen data.',
                           'FinText inputs are intraday percent returns, a proxy for daily excess returns.',
                           'No volume in source; Kronos OHLC-only mode.',
                           'Configured costs omit swap and variable slippage.']}
    path=Path(output);path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(report,allow_nan=False)+'\n')
    return {'samples':len(rows),'first':rows[0]['time'],'last':rows[-1]['time'],
            'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}


def load_predictor(name):
    import numpy as np
    import torch
    from huggingface_hub import snapshot_download
    torch.set_num_threads(2)
    torch.manual_seed(17);np.random.seed(17)
    repo=MODELS[name]
    try:path=snapshot_download(repo,local_files_only=True)
    except Exception:path=snapshot_download(repo,allow_patterns=['*.json','*.safetensors','*.bin','*.pt','*.pth'])
    metadata={'repo':repo,'revision':Path(path).name,'seed':17}
    if name.startswith('chronos'):
        from chronos import Chronos2Pipeline
        model=Chronos2Pipeline.from_pretrained(path,device_map='cpu')
        def predict(row):
            values=torch.tensor([r[4] for r in row['context']],dtype=torch.float32)
            output=model.predict([values],prediction_length=5)[0]
            # Chronos2 output is [target,quantile,horizon]. Select the 0.5 quantile.
            quantiles=list(model.quantiles)
            return output[0,quantiles.index(.5),:].cpu().tolist()
    elif name.startswith('kronos'):
        import sys
        import pandas as pd
        sys.path.insert(0,'/opt/kronos')
        from model import Kronos,KronosTokenizer,KronosPredictor
        tokenizer=KronosTokenizer.from_pretrained('NeoQuasar/Kronos-Tokenizer-base')
        model=KronosPredictor(Kronos.from_pretrained(path),tokenizer,device='cpu',max_context=512)
        metadata['sample_count']=1
        def predict(row):
            torch.manual_seed(17);np.random.seed(17)
            context=row['context']
            df=pd.DataFrame([r[1:] for r in context],columns=['open','high','low','close'])
            result=model.predict(df=df,x_timestamp=pd.Series(pd.to_datetime([r[0] for r in context],unit='s')),
                y_timestamp=pd.Series(pd.to_datetime(row['future_times'],unit='s')),
                pred_len=5,T=1.,top_p=.9,sample_count=1,verbose=False)
            return result['close'].tolist()
    elif name=='timesfm-3':
        from timesfm3 import TimesFM3Forecaster
        model=TimesFM3Forecaster.from_pretrained(path,device='cpu')
        def predict(row):
            output=model.predict(context=np.array([r[4] for r in row['context']],dtype=np.float32),horizon=5,return_quantiles=True)
            return np.asarray(output.forecast).reshape(-1)[:5].tolist()
    elif name=='timesfm-2.5':
        import timesfm
        model=timesfm.TimesFM_2p5_200M_torch.from_pretrained(path)
        model.compile(timesfm.ForecastConfig(max_context=256,max_horizon=32,normalize_inputs=True,
            use_continuous_quantile_head=True,force_flip_invariance=True,infer_is_positive=True,fix_quantile_crossing=True))
        def predict(row):
            result,_=model.forecast(horizon=5,inputs=[np.array([r[4] for r in row['context']],dtype=np.float32)])
            return np.asarray(result).reshape(-1)[:5].tolist()
    else:
        from chronos import ChronosPipeline
        model=ChronosPipeline.from_pretrained(path,device_map='cpu',torch_dtype=torch.float32)
        metadata['sample_count']=32
        metadata['input']='fractional simple returns; zero risk-free proxy; cumulative reconstruction'
        def predict(row):
            closes=np.array([r[4] for r in row['context']])
            returns=np.diff(closes)/closes[:-1]
            torch.manual_seed(17)
            output=model.predict(torch.tensor(returns,dtype=torch.float32),prediction_length=5,num_samples=32)
            paths=output[0].cpu().numpy()
            prices=row['reference']*np.cumprod(1+paths,axis=1)
            return np.median(prices,axis=0).tolist()
    return predict,metadata


def run_model(name,manifest,output):
    path=Path(output);path.parent.mkdir(parents=True,exist_ok=True)
    lock=path.with_suffix('.lock').open('w')
    fcntl.flock(lock,fcntl.LOCK_EX)
    document=json.loads(Path(manifest).read_text())
    digest=hashlib.sha256(Path(manifest).read_bytes()).hexdigest()
    if path.exists():
        cached=json.loads(path.read_text())
        if cached.get('status')=='complete' and cached.get('manifest_sha256')==digest:
            print(f'{name}: reusing complete frozen-manifest result',flush=True)
            return cached
    start=time.time()
    result={'name':name,'adapter_version':1,'manifest_sha256':digest,'status':'loading','predictions':[], 'live_activated':False}
    def save():
        temporary=path.with_suffix('.tmp')
        temporary.write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')
        temporary.replace(path)
    save()
    try:
        predictor,metadata=load_predictor(name)
        result.update(metadata=metadata,status='running',load_seconds=time.time()-start);save()
        import torch
        for row in document['samples']:
            before=time.time()
            with torch.inference_mode():predicted=predictor(row)
            if len(predicted)!=5 or not all(math.isfinite(v) and v>0 for v in predicted):
                raise ValueError('invalid five-step price forecast')
            result['predictions'].append({'time':row['time'],'close':predicted,'seconds':time.time()-before})
            save()
            if len(result['predictions'])%10==0:print(f'{name}: {len(result["predictions"])}/{len(document["samples"])}',flush=True)
        result['status']='complete'
    except Exception as exc:
        result.update(status='failed',error=f'{type(exc).__name__}: {exc}')
    result['elapsed_seconds']=time.time()-start;save()
    print(json.dumps({k:v for k,v in result.items() if k!='predictions'}),flush=True)
    return result


def side(price,reference,band):
    return 2 if price-reference>band else (0 if price-reference < -band else 1)


def score(document,predictions):
    import numpy as np
    rows=document['samples'];reports={}
    for horizon in (1,3,5):
        actual=[side(r['actual'][horizon-1],r['reference'],r['band']) for r in rows]
        pred=[side(p['close'][horizon-1],r['reference'],r['band']) for r,p in zip(rows,predictions)]
        recalls=[sum(a==c and p==c for a,p in zip(actual,pred))/sum(a==c for a in actual) for c in (0,1,2) if c in actual]
        net=[]
        for row,p in zip(rows,pred):
            if p==1:continue
            value=(row['actual'][horizon-1]-row['entry_bid']-row['entry_spread'] if p==2 else row['entry_bid']-row['actual'][horizon-1]-row['exit_spreads'][horizon-1]) - document['friction']
            net.append(value/row['atr'])
        gains=sum(v for v in net if v>0);losses=-sum(v for v in net if v<0)
        reports[str(horizon)]={'samples':len(rows),'balanced_accuracy':sum(recalls)/len(recalls),
           'accuracy':sum(a==p for a,p in zip(actual,pred))/len(rows),
           'class_counts':{str(c):actual.count(c) for c in (0,1,2)},
           'mae_atr':float(np.mean([abs(p['close'][horizon-1]-r['actual'][horizon-1])/r['atr'] for r,p in zip(rows,predictions)])),
           'trades':len(net),'profit_factor':gains/losses if losses else None,
           'mean_net_atr':float(np.mean(net)) if net else 0.,'total_net_atr':sum(net)}
    return reports


def summarize(manifest,directory):
    path=Path(manifest);document=json.loads(path.read_text());digest=hashlib.sha256(path.read_bytes()).hexdigest()
    results={};unavailable={}
    for name in MODELS:
        candidate=Path(directory)/f'{name}.json'
        if not candidate.exists():unavailable[name]='not run';continue
        report=json.loads(candidate.read_text())
        if report['status']!='complete':unavailable[name]=report.get('error',report['status']);continue
        predictions=report['predictions']
        if report['manifest_sha256']!=digest or [r['time'] for r in predictions]!=[r['time'] for r in document['samples']]:
            raise ValueError('mismatched evaluation samples')
        results[name]={'metrics':score(document,predictions),'elapsed_seconds':report['elapsed_seconds'],'metadata':report['metadata']}
    for name in ('persistence','last_return'):
        predictions=[]
        for row in document['samples']:
            delta=row['context'][-1][4]-row['context'][-2][4] if name=='last_return' else 0
            predictions.append({'close':[row['reference']+delta*h for h in range(1,6)]})
        results[name]={'metrics':score(document,predictions)}
    report={'manifest_sha256':digest,'samples':len(document['samples']),'results':results,
            'unavailable':unavailable,'scope':document['scope'],'limitations':document['limitations'],
            'ranking_note':'Exploratory only; no tuning on results. More forward samples required for winner selection.',
            'live_activated':False}
    (Path(directory)/'summary.json').write_text(json.dumps(report,indent=2,allow_nan=False)+'\n')
    return report


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('mode',choices=['prepare','run','summarize'])
    p.add_argument('--db',default='data/ramon_history.sqlite3')
    p.add_argument('--manifest',default='data/research/foundation_v1/manifest.json')
    p.add_argument('--out',default='data/research/foundation_v1')
    p.add_argument('--model',choices=MODELS)
    p.add_argument('--samples',type=int,default=120)
    a=p.parse_args()
    if a.mode=='prepare':print(json.dumps(prepare(a.db,a.manifest,count=a.samples)))
    elif a.mode=='run':run_model(a.model,a.manifest,Path(a.out)/f'{a.model}.json')
    else:print(json.dumps(summarize(a.manifest,a.out),indent=2))
