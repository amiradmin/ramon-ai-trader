"""Bounded public tick preview audit; never alters execution or downloads full history."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import json
from math import isfinite
from pathlib import Path
from statistics import mean, median
from urllib.parse import urlencode
from urllib.request import urlopen

DATASET='CarlosSilva1/xauusd-ticks'


def audit_rows(rows, *, point=.01):
    if not isfinite(point) or point<=0:
        raise ValueError('invalid reference point')
    bad=backward=duplicates=zero_spread=naive=0
    spreads=[]; times=[]; previous=None
    for row in rows:
        try:
            t=datetime.fromisoformat(row['timestamp'])
            if t.tzinfo is None:
                naive+=1
                # Dataset card declares UTC; not an independently verified clock.
                t=t.replace(tzinfo=timezone.utc)
            t=t.astimezone(timezone.utc)
            bid,ask=float(row['bid_price']),float(row['ask_price'])
            if not isfinite(bid) or not isfinite(ask) or bid<=0 or ask<bid:
                raise ValueError('invalid quote')
        except (KeyError,TypeError,ValueError):
            bad+=1
            continue
        if previous is not None:
            backward+=t<previous
            duplicates+=t==previous
        previous=t;times.append(t)
        spread=ask-bid
        zero_spread+=spread==0
        spreads.append(spread)
    return {'rows':len(rows),'valid_rows':len(spreads),'invalid_rows':bad,
            'backward_timestamps':backward,'duplicate_timestamps':duplicates,
            'zero_spreads':zero_spread,'naive_timestamps':naive,
            'first_utc':times[0].isoformat() if times else None,
            'last_utc':times[-1].isoformat() if times else None,
            'spread_price':{'min':min(spreads) if spreads else None,
                            'median':median(spreads) if spreads else None,
                            'mean':mean(spreads) if spreads else None,
                            'max':max(spreads) if spreads else None},
            'reference_point':point,
            'median_spread_reference_points':median(spreads)/point if spreads else None,
            'can_calibrate_execution':False}


def fetch_json(url):
    with urlopen(url,timeout=30) as response:
        return json.load(response)


def run(output_dir='data/tick_sample_audit', offsets=(0,1000000,100000000)):
    destination=Path(output_dir);destination.mkdir(parents=True,exist_ok=True)
    revision=fetch_json(f'https://huggingface.co/api/datasets/{DATASET}')['sha']
    report={'dataset':DATASET,'observed_revision':revision,'license':'CC-BY-4.0',
        'attribution':'Silva, C. (2026), XAU/USD Tick Data, Hugging Face.',
        'source_url':f'https://huggingface.co/datasets/{DATASET}',
        'timezone_basis':'dataset card declares UTC; preview timestamps are naive',
        'sample_type':'three bounded viewer windows, at most 100 rows each',
        'revision_binding':'viewer response not cryptographically tied to repository revision',
        'limitations':['Small selected windows are not representative of the whole feed.',
                       'Source describes reconstructed broker feed; broker is not independently verified.',
                       'No LiteFinance equivalence, fill/slippage calibration or live use established.'],
        'windows':[]}
    for offset in offsets:
        if offset<0:
            raise ValueError('negative offset')
        url='https://datasets-server.huggingface.co/rows?'+urlencode({
            'dataset':DATASET,'config':'default','split':'train','offset':offset,'length':100})
        try:
            response=fetch_json(url)
            rows=[item['row'] for item in response['rows']]
            if not rows or len(rows)>100:
                raise ValueError('invalid bounded sample length')
            payload=json.dumps(response,sort_keys=True,allow_nan=False)
            path=destination/f'offset_{offset}.json'
            path.write_text(payload+'\n')
            report['windows'].append({'offset':offset,'audit':audit_rows(rows),
                'sha256':hashlib.sha256(payload.encode()).hexdigest(),'sample_file':str(path)})
        except Exception as exc:
            report['windows'].append({'offset':offset,'error':f'{type(exc).__name__}: {exc}'})
    final_revision=fetch_json(f'https://huggingface.co/api/datasets/{DATASET}')['sha']
    report['revision_unchanged_during_fetch']=revision==final_revision
    report['fetched_rows']=sum(w.get('audit',{}).get('rows',0) for w in report['windows'])
    report['usable_for_initial_inspection']=report['fetched_rows']>0 and all(
        w.get('audit',{}).get('invalid_rows',0)==0 and w.get('audit',{}).get('backward_timestamps',0)==0
        for w in report['windows']) and report['revision_unchanged_during_fetch']
    report['can_calibrate_execution']=False
    (destination/'report.json').write_text(json.dumps(report,indent=2,allow_nan=False)+'\n')
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir',default='data/tick_sample_audit')
    args=parser.parse_args()
    print(json.dumps(run(args.output_dir),indent=2))


if __name__=='__main__':main()
