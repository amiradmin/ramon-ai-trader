"""Paired forward-only research recorder for TimesFM 3 and seeded Kronos-small.

No trading-policy, order, or broker execution imports. Source database is read-only.
"""
from __future__ import annotations
import argparse
from dataclasses import dataclass
import fcntl
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import time
import zlib

from .core import Bar, atr14
from .foundation_benchmark import side
from .kronos_direction_shadow import acceptable_gap

HORIZONS = (1, 3, 5)
SEEDS = (17, 29, 43)
REVISIONS = {
    'timesfm-3': ('google/timesfm-3.0-pytorch', '43046b85ec22d584a13f8098c2ed39c889e129c2'),
    'kronos-small': ('NeoQuasar/Kronos-small', '901c26c1332695a2a8f243eb2f37243a37bea320'),
}
PROTOCOL = {'schema': 'foundation-forward-v1', 'horizons': HORIZONS, 'lookback': 256,
            'seeds': SEEDS, 'paths_per_seed': 4, 'friction': .1, 'fresh_seconds': 120,
            'completion_seconds': 180, 'minimum_origin_spacing_bars': 5,
            'revisions': REVISIONS, 'purpose': 'research_only_no_orders'}
PROTOCOL_ID = hashlib.sha256(json.dumps(PROTOCOL, sort_keys=True).encode()).hexdigest()
SCHEMA = '''
CREATE TABLE IF NOT EXISTS origins(
 protocol TEXT, symbol TEXT, bar INTEGER, audit_key TEXT, input_sha TEXT,
 recorded REAL, completed REAL, offset INTEGER, reference REAL, atr REAL, band REAL,
 status TEXT, error TEXT, PRIMARY KEY(protocol,symbol,bar));
CREATE TABLE IF NOT EXISTS forecasts(
 protocol TEXT, symbol TEXT, bar INTEGER, model TEXT, path TEXT, diagnostics TEXT,
 PRIMARY KEY(protocol,symbol,bar,model));
CREATE TABLE IF NOT EXISTS outcomes(
 protocol TEXT, symbol TEXT, bar INTEGER, horizon INTEGER, actual REAL, label INTEGER,
 entry REAL, entry_spread REAL, exit_spread REAL, status TEXT,
 PRIMARY KEY(protocol,symbol,bar,horizon));
'''


@dataclass(frozen=True)
class Candidate:
    symbol: str
    bar: int
    offset: int
    reference: float
    atr: float
    band: float
    row: dict


def candidate(context, provenance, recorded, now):
    request = provenance['request']
    if context.get('timeframe') != 'M15' or context.get('symbol') != request.get('symbol'):
        raise ValueError('symbol/timeframe mismatch')
    offset = request.get('broker_utc_offset_seconds')
    quote = request.get('quote_time')
    if type(offset) is not int or abs(offset) > 14*3600 or offset % 900 or type(quote) is not int:
        raise ValueError('missing/invalid broker clock')
    if not 0 <= now-recorded <= PROTOCOL['fresh_seconds'] or abs(now-(quote-offset)) > 120:
        raise ValueError('stale audit/quote')
    bars = tuple(Bar(**b) for b in context['bars'][-256:])
    if len(bars) != 256:
        raise ValueError('need 256 completed bars')
    for b in bars:
        if not all(math.isfinite(v) and v > 0 for v in (b.open,b.high,b.low,b.close)):
            raise ValueError('non-finite or nonpositive OHLC')
        if not b.low <= min(b.open,b.close) <= max(b.open,b.close) <= b.high:
            raise ValueError('invalid OHLC geometry')
    if any(b.time <= a.time or not acceptable_gap(a.time,b.time) for a,b in zip(bars,bars[1:])):
        raise ValueError('context gap')
    if any(b.time-a.time != 900 for a,b in zip(bars[-64:],bars[-63:])):
        raise ValueError('recent context gap')
    close_time = bars[-1].time+900
    if not 0 <= quote-close_time <= 120 or not 0 <= now+offset-close_time <= 120:
        raise ValueError('not a fresh completed boundary')
    scale = atr14(bars[-64:])
    point = float(request['point'])
    spread = float(request['ask'])-float(request['bid'])
    if not math.isfinite(spread) or spread < 0 or not math.isfinite(point) or abs(point-.01) > 1e-9 or scale <= 0:
        raise ValueError('invalid spread, point, or ATR')
    band = max(.2*scale,spread+PROTOCOL['friction'])
    row = {'reference':bars[-1].close,
           'context':[[b.time-offset,b.open,b.high,b.low,b.close] for b in bars],
           'future_times':[bars[-1].time-offset+900*i for i in range(1,6)]}
    return Candidate(context['symbol'],bars[-1].time,offset,bars[-1].close,scale,band,row)


def load_models():
    import numpy as np
    import pandas as pd
    import torch
    from huggingface_hub import snapshot_download
    from timesfm3 import TimesFM3Forecaster
    import sys
    sys.path.insert(0,'/opt/kronos')
    from model import Kronos, KronosTokenizer, KronosPredictor
    torch.set_num_threads(2)
    paths = {name:snapshot_download(repo,revision=revision,local_files_only=True)
             for name,(repo,revision) in REVISIONS.items()}
    tokenizer_path = snapshot_download('NeoQuasar/Kronos-Tokenizer-base',local_files_only=True)
    tokenizer = KronosTokenizer.from_pretrained(tokenizer_path)
    kronos = KronosPredictor(Kronos.from_pretrained(paths['kronos-small']),tokenizer,
                             device='cpu',max_context=512)
    timesfm = TimesFM3Forecaster.from_pretrained(paths['timesfm-3'],device='cpu')

    def predict(c):
        row = c.row
        with torch.inference_mode():
            output = timesfm.predict(context=np.asarray([r[4] for r in row['context']],dtype=np.float32),
                                     horizon=5,return_quantiles=True)
            times_path = np.asarray(output.forecast).reshape(-1)[:5].tolist()
            context = row['context']
            frame = pd.DataFrame([r[1:] for r in context],columns=['open','high','low','close'])
            stamps = pd.Series(pd.to_datetime([r[0] for r in context],unit='s'))
            future = pd.Series(pd.to_datetime(row['future_times'],unit='s'))
            paths_by_seed = {}
            for seed in SEEDS:
                torch.manual_seed(seed);np.random.seed(seed)
                result = kronos.predict(df=frame,x_timestamp=stamps,y_timestamp=future,pred_len=5,
                    T=1.,top_p=.9,sample_count=PROTOCOL['paths_per_seed'],verbose=False)
                paths_by_seed[str(seed)] = result['close'].tolist()
            ensemble = np.median(list(paths_by_seed.values()),axis=0).tolist()
        return {'timesfm-3':{'path':times_path,'diagnostics':{}},
                'kronos-small':{'path':ensemble,'diagnostics':{'seed_paths':paths_by_seed,
                    'paths_per_seed':PROTOCOL['paths_per_seed'], 'aggregation':'median_of_seed_mean_paths'}}}
    return predict


class Recorder:
    def __init__(self,history,output,predictor,*,now=None):
        self.history = Path(history).resolve()
        self.output = Path(output);self.output.parent.mkdir(parents=True,exist_ok=True)
        self.predictor = predictor
        self.cursor = time.time() if now is None else now
        with sqlite3.connect(self.output) as out:
            out.executescript(SCHEMA)
            # Crashed/incomplete origins are never backfilled as new predictions.
            out.execute("UPDATE origins SET status='interrupted',error='recorder restarted' WHERE status='computing'")

    def tick(self,now=None):
        clock = time.time if now is None else lambda:now
        now = clock()
        accepted = skipped = 0
        with sqlite3.connect(self.history.as_uri()+'?mode=ro',uri=True,timeout=2) as source:
            audits = source.execute('''SELECT a.sample_key,a.recorded_utc,a.input_sha256,a.provenance_json,b.codec,b.body
                FROM inference_audit a JOIN input_blobs b ON b.sha256=a.input_sha256
                WHERE a.recorded_utc>? ORDER BY a.recorded_utc LIMIT 1000''',(self.cursor,)).fetchall()
            for key,recorded,digest,raw,codec,blob in audits:
                self.cursor = max(self.cursor,recorded)
                try:
                    if codec != 'zlib-json-v1':raise ValueError('unsupported codec')
                    c = candidate(json.loads(zlib.decompress(blob)),json.loads(raw),recorded,clock())
                except (ValueError,TypeError,KeyError,OverflowError,zlib.error):
                    skipped += 1;continue
                with sqlite3.connect(self.output,timeout=2) as out:
                    previous = out.execute('SELECT MAX(bar) FROM origins WHERE protocol=? AND symbol=?',
                                           (PROTOCOL_ID,c.symbol)).fetchone()[0]
                    if previous is not None and c.bar-previous < 4500:
                        continue
                    out.execute('INSERT INTO origins VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                        (PROTOCOL_ID,c.symbol,c.bar,key,digest,clock(),None,c.offset,c.reference,c.atr,c.band,'computing',None))
                try:
                    forecasts = self.predictor(c)
                    completed = clock()
                    if set(forecasts) != set(REVISIONS):raise ValueError('missing paired model')
                    for result in forecasts.values():
                        if len(result['path']) != 5 or not all(math.isfinite(v) and v>0 for v in result['path']):
                            raise ValueError('invalid five-step forecast')
                    if completed+c.offset-(c.bar+900) > PROTOCOL['completion_seconds']:
                        raise ValueError('paired prediction missed predeclared deadline')
                    with sqlite3.connect(self.output,timeout=2) as out:
                        for model,result in forecasts.items():
                            out.execute('INSERT INTO forecasts VALUES (?,?,?,?,?,?)',
                                (PROTOCOL_ID,c.symbol,c.bar,model,json.dumps(result['path']),json.dumps(result['diagnostics'])))
                        out.execute("UPDATE origins SET status='pending',completed=? WHERE protocol=? AND symbol=? AND bar=?",
                                    (completed,PROTOCOL_ID,c.symbol,c.bar))
                    accepted += 1
                except Exception as exc:
                    with sqlite3.connect(self.output,timeout=2) as out:
                        out.execute("UPDATE origins SET status='failed',completed=?,error=? WHERE protocol=? AND symbol=? AND bar=?",
                                    (clock(),f'{type(exc).__name__}: {exc}'[:500],PROTOCOL_ID,c.symbol,c.bar))
                    print(json.dumps({'status':'prediction_failed','bar':c.bar,'error':str(exc)[:300]}),flush=True)
            self.settle(source,clock())
        return {'accepted_pairs':accepted,'skipped_audits':skipped,'cursor':self.cursor}

    def settle(self,source,now):
        with sqlite3.connect(self.output,timeout=2) as out:
            pending = out.execute("SELECT symbol,bar,offset,reference,band FROM origins WHERE protocol=? AND status='pending'",(PROTOCOL_ID,)).fetchall()
            for symbol,bar,offset,reference,band in pending:
                for horizon in HORIZONS:
                    if now+offset < bar+(horizon+1)*900:continue
                    if out.execute('SELECT 1 FROM outcomes WHERE protocol=? AND symbol=? AND bar=? AND horizon=?',
                                   (PROTOCOL_ID,symbol,bar,horizon)).fetchone():continue
                    actual = source.execute('''SELECT time,open,close,COALESCE(spread_points,0) FROM history_bars
                        WHERE symbol=? AND timeframe='M15' AND time BETWEEN ? AND ? ORDER BY time''',
                        (symbol,bar,bar+horizon*900)).fetchall()
                    if [r[0] for r in actual] != [bar+i*900 for i in range(horizon+1)]:
                        if now+offset > bar+(horizon+1)*900+86400:
                            out.execute('INSERT INTO outcomes VALUES (?,?,?,?,?,?,?,?,?,?)',
                                        (PROTOCOL_ID,symbol,bar,horizon,None,None,None,None,None,'missing_history'))
                        continue
                    value = actual[-1][2]
                    spread = lambda r:(r[3] if r[3]>0 else 42)*.01
                    out.execute('INSERT INTO outcomes VALUES (?,?,?,?,?,?,?,?,?,?)',
                        (PROTOCOL_ID,symbol,bar,horizon,value,side(value,reference,band),actual[1][1],
                         spread(actual[1]),spread(actual[-1]),'settled'))
                count = out.execute('SELECT count(*) FROM outcomes WHERE protocol=? AND symbol=? AND bar=?',
                                    (PROTOCOL_ID,symbol,bar)).fetchone()[0]
                if count == len(HORIZONS):
                    out.execute("UPDATE origins SET status='resolved' WHERE protocol=? AND symbol=? AND bar=?",(PROTOCOL_ID,symbol,bar))


def summarize(path):
    report = {'protocol_id':PROTOCOL_ID,'protocol':PROTOCOL,'research_only':True,
              'live_trading':False,'horizons':{}}
    with sqlite3.connect(path) as con:
        report['states'] = dict(con.execute('SELECT status,count(*) FROM origins WHERE protocol=? GROUP BY status',(PROTOCOL_ID,)))
        for horizon in HORIZONS:
            rows = con.execute('''SELECT o.reference,o.band,o.atr,u.actual,u.label,u.entry,u.entry_spread,u.exit_spread,
                    t.path,k.path FROM origins o JOIN outcomes u USING(protocol,symbol,bar)
                    JOIN forecasts t USING(protocol,symbol,bar) JOIN forecasts k USING(protocol,symbol,bar)
                    WHERE o.protocol=? AND u.horizon=? AND u.status='settled'
                    AND t.model='timesfm-3' AND k.model='kronos-small' ''',(PROTOCOL_ID,horizon)).fetchall()
            result = {'matched_samples':len(rows),'models':{}}
            for model,index in [('timesfm-3',8),('kronos-small',9)]:
                if not rows:continue
                predicted = [side(json.loads(r[index])[horizon-1],r[0],r[1]) for r in rows]
                labels = [r[4] for r in rows]
                net = []
                for r,p in zip(rows,predicted):
                    if p == 1:continue
                    value = r[3]-r[5]-r[6] if p == 2 else r[5]-r[3]-r[7]
                    net.append((value-PROTOCOL['friction'])/r[2])
                gains = sum(v for v in net if v>0);losses = -sum(v for v in net if v<0)
                recalls = [sum(a==c and p==c for a,p in zip(labels,predicted))/labels.count(c) for c in (0,1,2) if c in labels]
                result['models'][model] = {
                    'accuracy':sum(a==p for a,p in zip(labels,predicted))/len(rows),
                    'balanced_accuracy':sum(recalls)/3 if len(recalls)==3 else None,
                    'mae_atr':sum(abs(json.loads(r[index])[horizon-1]-r[3])/r[2] for r in rows)/len(rows),
                    'trades':len(net),'profit_factor':gains/losses if losses else None,
                    'total_net_atr':sum(net),'mean_net_atr':sum(net)/len(net) if net else 0}
            report['horizons'][str(horizon)] = result
    report['limitations'] = ['No winner until sufficient new forward evidence; adjacent contexts remain correlated.',
        'Execution is hypothetical next open with spread and fixed friction, no actual fills.',
        'Next-open execution is optimistic if inference completes after first next-bar ticks.',
        'Kronos aggregates 3 seeded means of 4 paths, not the single-path historical policy.',
        'TimesFM 3 downloaded weights are research-only; no trading activation.']
    return report


def publish(path,value):
    path = Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value,indent=2,allow_nan=False)+'\n')
    temporary.replace(path)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--history',default='/history/ramon_history.sqlite3')
    p.add_argument('--output',default='/results/forward.sqlite3')
    p.add_argument('--summary',action='store_true')
    p.add_argument('--once',action='store_true')
    a = p.parse_args()
    if a.summary:print(json.dumps(summarize(a.output),indent=2));return
    path = Path(a.output);path.parent.mkdir(parents=True,exist_ok=True)
    lock = path.with_suffix('.lock').open('w');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    publish(path.with_name('status.json'),{'state':'loading','protocol':PROTOCOL,'updated_utc':time.time()})
    predictor = load_models()
    # Start after model loading; old audits are never replayed as forward predictions.
    recorder = Recorder(a.history,a.output,predictor)
    print(json.dumps({'status':'ready','protocol_id':PROTOCOL_ID,'live_trading':False}),flush=True)
    while True:
        try:
            tick = recorder.tick()
            report = summarize(a.output)
            publish(path.with_name('summary.json'),report)
            publish(path.with_name('status.json'),{'state':'watching','updated_utc':time.time(),**tick,
                'origins':report['states'],'live_trading':False})
            if tick['accepted_pairs']:print(json.dumps(tick),flush=True)
        except sqlite3.OperationalError as exc:
            publish(path.with_name('status.json'),{'state':'retry','updated_utc':time.time(),'error':str(exc)})
            if a.once:raise
        if a.once:return
        time.sleep(5)


if __name__ == '__main__':main()
