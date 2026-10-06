"""Read-only ledger of positive model edges; never authorizes an order."""
import json
import sqlite3
import time
from pathlib import Path


ACTIONABLE_SECONDS = 90


DIRECTION_MIN_QUALITY = 0.55
DIRECTION_MIN_MARGIN = 0.05


def _direction_probabilities(audit):
    try:
        quality = audit.get("shadow_forecasts", {}).get("direction_quality", {})
        buy = float(quality.get("buy_success_probability"))
        sell = float(quality.get("sell_success_probability"))
        if not (0.0 <= buy <= 1.0 and 0.0 <= sell <= 1.0):
            return None, None
        return buy, sell
    except (TypeError, ValueError):
        return None, None


def _live_direction(audit):
    """Operational BUY/SELL/WAIT selector from trained direction-quality models."""
    buy, sell = _direction_probabilities(audit)
    if buy is None or sell is None:
        return "WAIT", None, None, None
    best = max(buy, sell)
    margin = abs(buy - sell)
    if best < DIRECTION_MIN_QUALITY or margin < DIRECTION_MIN_MARGIN:
        return "WAIT", best, buy, sell
    return ("BUY" if buy > sell else "SELL"), best, buy, sell


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
                side,probability,buy_probability,sell_probability=_live_direction(audit)
                if side not in {'BUY','SELL'} or not row['quote_time']:
                    continue
                edge=base['buy_edge'] if side=='BUY' else base['sell_edge']
                # Direction quality is authoritative for table direction.
                # Chronos edge remains telemetry/geometry; a non-positive edge
                # forces a fresh RECHECK instead of hiding or flipping the row.
                key=row['signal_bar_time']
                sign=1 if side=='BUY' else -1
                entry=row['mid']+sign*row['spread']/2
                # Use MAIN geometry, never substitute a range decision's levels.
                stop=base.get('stop_distance');target=base.get('target_distance')
                if not stop or not target:continue
                first_reason = grouped[key]['first_reason'] if key in grouped else final.get('reason')
                prior_executed = grouped[key]['executed'] if key in grouped else False
                prior_approved = grouped[key]['model_approved'] if key in grouped else False
                grouped[key]={'captured':row['captured'],'latest_captured':row['captured'],'sample_key':row['sample_key'],
                    'signal_bar_time':row['signal_bar_time'],'quote_time':row['quote_time'],
                    'direction':side,'strategy':'برگشت تأییدشده' if base.get('trend_conflict_active')==1 and base.get('intrabar_confirmed')==1 and base.get('ai_trend_confirmed')==1 else 'پیش‌بینی مدل',
                    'edge':edge,'minimum_edge':base.get('minimum_edge'),'strength':base.get('signal_strength'),
                    'success_probability':probability,
                    'buy_success_probability':buy_probability,
                    'sell_success_probability':sell_probability,
                    'direction_source':'LIVE_DIRECTION_QUALITY',
                    'entry':entry,'stop':entry-sign*stop,'target':entry+sign*target,
                    'risk_distance':stop,'first_reason':first_reason,'last_reason':final.get('reason'),
                    'model_approved':prior_approved or row['final_decision']==side,
                    'executed':prior_executed or row['sample_key'] in traded,
                    'outcome':'OPEN','net_r':None}
            except (ValueError,TypeError,KeyError):continue
        now=int(time.time())
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
            item['age_seconds']=max(0,now-int(item['latest_captured']))
            item['actionable']=bool(
                item['outcome']=='OPEN' and not item['executed']
                and item['age_seconds']<=ACTIONABLE_SECONDS
                and item['edge']>0
            )
        return {'opportunities':sorted(grouped.values(),key=lambda x:x['captured'],reverse=True)[:max(1,min(limit,200))],
                'as_of':latest,'actionable_seconds':ACTIONABLE_SECONDS,
                'scope':'LIVE_DIRECTION_QUALITY selects BUY/SELL with quality>=0.55 and margin>=0.05; historical opportunity ledger supplies geometry; quote sampled; spread included; no commission/slippage; manual execution requires fresh EA-side revalidation'}
    except sqlite3.Error as exc:
        return {'opportunities':[], 'error':str(exc),'scope':'positive_model_edge'}
    finally:con.close()
