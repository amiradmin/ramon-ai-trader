"""Paired exit experiment: frozen baseline entries, no additional re-entries."""
import sys, json, hashlib
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'backend/django_bridge'))
from ramon_pipeline.signalflow.profit_protection import ProfitProtection

def resolve(t,bars,a,policy):
    sign=1 if t['side']=='BUY' else -1
    point=a['point']; spread=50*point; slip=5*point
    fill=t['entry']; entry=fill-sign*slip; original=t['stop']; risk=sign*(entry-original)
    stop=original; target=t['target']; fraction=1.; realized=0.
    if policy=='target_1R': target=entry+sign*risk
    if policy=='target_1_2R': target=entry+sign*min(sign*(target-entry),1.2*risk)
    rule=ProfitProtection(trailing_trigger_r=1.0) if policy=='trail_1R' else ProfitProtection()
    start=next(i for i,b in enumerate(bars) if b['time']==t['entry_time'])
    window=bars[start:start+24]
    if len(window)!=24 or any(b['time']!=t['entry_time']+i*900 for i,b in enumerate(window)):
        return None
    for b in window:
        op,lo,hi,close=[b[k]+(spread if sign<0 else 0) for k in ['open','low','high','close']]
        reason=None
        if sign*(op-stop)<=0: price=op; reason='STOP_GAP'
        elif sign*(op-target)>=0: price=target; reason='TARGET'
        elif (lo<=stop if sign>0 else hi>=stop): price=stop; reason='STOP'
        elif (hi>=target if sign>0 else lo<=target): price=target; reason='TARGET'
        if reason:
            if policy=='partial_1R' and fraction==1 and reason=='TARGET' and sign*(target-entry)>=risk:
                realized=.5*sign*(entry+sign*risk-sign*slip-fill)/risk
                fraction=.5
            net=realized+fraction*sign*(price-sign*slip-fill)/risk
            return dict(net_r=net,reason=reason)
        favorable=hi if sign>0 else lo
        if policy=='partial_1R' and fraction==1 and sign*(favorable-entry)>=risk:
            realized=.5*sign*(entry+sign*risk-sign*slip-fill)/risk
            fraction=.5
        if policy in {'breakeven_1R','trail_1R'}:
            stop=rule.stop(side=t['side'],entry=fill,original_risk=sign*(fill-original),current_stop=stop,favorable_price=favorable)
    return dict(net_r=realized+fraction*sign*(close-sign*slip-fill)/risk,reason='TIME_EXIT')

def main():
    path=Path(__file__).with_name('snapshot.json'); s=json.loads(path.read_text())
    results={}; eligible=[]
    for t in s['baseline']['trades']:
        if resolve(t,s['bars'],s['account'],'current') is not None: eligible.append(t)
    for policy in ['current','target_1_2R','target_1R','breakeven_1R','trail_1R','partial_1R']:
        rows=[resolve(t,s['bars'],s['account'],policy) for t in eligible]
        rs=[r['net_r'] for r in rows]; equity=peak=dd=0.
        for r in rs: equity+=r; peak=max(peak,equity); dd=max(dd,peak-equity)
        results[policy]=dict(trades=len(rs),net_r=sum(rs),expectancy_r=sum(rs)/len(rs) if rs else None,
            win_rate=sum(r>0 for r in rs)/len(rs) if rs else None,max_closed_cumulative_r_drawdown=dd,
            outcomes={reason:sum(r['reason']==reason for r in rows) for reason in ['STOP','STOP_GAP','TARGET','TIME_EXIT']})
    # Original risk / causal ATR from the 14 completed bars before the entry.
    groups={}
    for t in eligible:
        i=next(i for i,b in enumerate(s['bars']) if b['time']==t['entry_time'])
        atr=sum(max(s['bars'][j]['high']-s['bars'][j]['low'],abs(s['bars'][j]['high']-s['bars'][j-1]['close']),abs(s['bars'][j]['low']-s['bars'][j-1]['close'])) for j in range(i-14,i))/14
        entry=t['entry']-(1 if t['side']=='BUY' else -1)*.05
        key='above_2_ATR' if abs(entry-t['stop'])/atr>2 else 'at_most_2_ATR'
        groups.setdefault(key,[]).append(resolve(t,s['bars'],s['account'],'current')['net_r'])
    report=dict(validation='DESCRIPTIVE_PAIRED_EXITS_NOT_OUT_OF_SAMPLE_OR_PORTFOLIO',snapshot_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        source=s['source'],symbol=s['symbol'],first_bar=s['bars'][0]['time'],last_bar=s['bars'][-1]['time'],
        input_bars=len(s['bars']),off_grid_excluded=s['excluded_off_grid'],baseline_trades=len(s['baseline']['trades']),
        excluded_incomplete_or_gapped_windows=len(s['baseline']['trades'])-len(eligible),config=s['config'],results=results,
        wide_stop_groups={k:dict(trades=len(v),net_r=sum(v),expectancy_r=sum(v)/len(v)) for k,v in groups.items()},
        assumptions=dict(spread_points=50,slippage_points_each_side=5,commission_points=0,holding_bars=24,
        partial='IDEAL_HALF_VOLUME_NO_BROKER_LOT_ROUNDING',protection='NEXT_BAR_ACTIVATION_STOP_FIRST',
        exposure='FROZEN_BASELINE_ENTRIES_NO_EXTRA_REENTRIES',drawdown='CUMULATIVE_R_CLOSED_OUTCOMES_NOT_PERCENT_OR_INTRATRADE'))
    Path(__file__).with_name('results.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:report[k] for k in ['results','wide_stop_groups','baseline_trades','excluded_incomplete_or_gapped_windows']},indent=2))
if __name__=='__main__': main()
