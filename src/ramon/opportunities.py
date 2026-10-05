"""Read-only ledger of positive model edges; never authorizes an order."""
import json
import sqlite3
from pathlib import Path


def read_opportunities(db, symbol='XAUUSD_l', limit=200):
    path=Path(db)
    if not path.exists():
        return {'opportunities':[], 'scope':'positive_model_edge', 'error':'history unavailable'}
    con=sqlite3.connect(f'file:{path.resolve()}?mode=ro',uri=True,timeout=.5)
    con.row_factory=sqlite3.Row
    try:
        latest=con.execute('SELECT max(captured) FROM decision_samples WHERE symbol=?',(symbol,)).fetchone()[0]
        if latest is None:return {'opportunities':[], 'scope':'positive_model_edge'}
        rows=list(con.execute('''SELECT captured,signal_bar_time,sample_key,quote_time,mid,spread,
            stop_distance,target_distance,final_decision,model_metadata FROM decision_samples
            WHERE symbol=? AND captured>=? ORDER BY captured,id''',(symbol,latest-86400)))
        traded={r[0] for r in con.execute('SELECT sample_key FROM trade_outcomes WHERE symbol=?',(symbol,))}
        grouped={}
        for row in rows:
            try:
                metadata=json.loads(row['model_metadata'] or '{}');audit=metadata.get('decision_audit',{});base=audit.get('base',{});final=audit.get('final',{})
                if not base.get('forecast_median',0)>0:continue
                side='BUY' if base['buy_edge']>base['sell_edge'] else 'SELL'
                edge=max(base['buy_edge'],base['sell_edge'])
                if edge<=0 or not row['quote_time']:continue
                key=(row['signal_bar_time'],side)
                if key in grouped:
                    grouped[key]['last_reason']=final.get('reason')
                    grouped[key]['model_approved'] |= row['final_decision']==side
                    grouped[key]['executed'] |= row['sample_key'] in traded
                    continue
                sign=1 if side=='BUY' else -1
                entry=row['mid']+sign*row['spread']/2
                # Use MAIN geometry, never substitute a range decision's levels.
                stop=base.get('stop_distance');target=base.get('target_distance')
                if not stop or not target:continue
                grouped[key]={'captured':row['captured'],'sample_key':row['sample_key'],
                    'signal_bar_time':row['signal_bar_time'],'quote_time':row['quote_time'],
                    'direction':side,'strategy':'برگشت تأییدشده' if base.get('trend_conflict_active')==1 and base.get('intrabar_confirmed')==1 and base.get('ai_trend_confirmed')==1 else 'پیش‌بینی مدل',
                    'edge':edge,'minimum_edge':base.get('minimum_edge'),'strength':base.get('signal_strength'),
                    'entry':entry,'stop':entry-sign*stop,'target':entry+sign*target,
                    'risk_distance':stop,'first_reason':final.get('reason'),'last_reason':final.get('reason'),
                    'model_approved':row['final_decision']==side,'executed':row['sample_key'] in traded,
                    'outcome':'OPEN','net_r':None}
            except (ValueError,TypeError,KeyError):continue
        for item in grouped.values():
            previous=item['quote_time'];sign=1 if item['direction']=='BUY' else -1
            for row in rows:
                q=row['quote_time']
                if q is None or q<=previous:continue
                if q-previous>120:
                    item['outcome']='DATA_GAP';break
                previous=q
                mark=row['mid']-sign*row['spread']/2
                item['net_r']=sign*(mark-item['entry'])/item['risk_distance']
                if sign*(mark-item['stop'])<=0:item['outcome']='SL_OBSERVED';break
                if sign*(mark-item['target'])>=0:item['outcome']='TP_OBSERVED';break
                if q-item['quote_time']>=14400:item['outcome']='TIMEOUT_OBSERVED';break
        return {'opportunities':sorted(grouped.values(),key=lambda x:x['captured'],reverse=True)[:max(1,min(limit,200))],
                'as_of':latest,'scope':'positive_model_edge; first snapshot per M15/direction; quote sampled; spread included; no commission/slippage; not order authorization'}
    except sqlite3.Error as exc:
        return {'opportunities':[], 'error':str(exc),'scope':'positive_model_edge'}
    finally:con.close()
