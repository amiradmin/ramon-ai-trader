"""Read-only chronological comparison using recorded forecasts and real inputs.

No model weights, synthetic M1 candles, orders, or writes to the history store.
The payoff is a coarse M15 SL/TP sensitivity analysis, not MT5 TP-stage P/L.
"""
from __future__ import annotations

import argparse
from bisect import bisect_left, bisect_right
from collections import Counter
from dataclasses import asdict, replace
import hashlib
import json
from pathlib import Path
import sqlite3
import zlib

from .core import Bar, Forecast, Market, Settings, evaluate
from .market_state import assess_market, apply_market_policy
from .range_strategy import live_candidate


class RecordedForecast:
    def __init__(self, forecast):
        self.value = forecast

    def forecast(self, closes, horizon):
        if len(self.value.median_path) != horizon:
            raise ValueError('recorded forecast horizon mismatch')
        return self.value


def route_recorded(market, forecaster, settings, *, quote_time, range_enabled=False):
    """Same model/range/policy ordering as the live shadow-mode service."""
    response = evaluate(market, forecaster, settings).to_dict()
    setup = live_candidate(market, response, enabled=range_enabled, capable=range_enabled,
                           quote_time=quote_time, max_spread_points=settings.max_spread_points)
    response['range_execution'] = int(setup is not None)
    if setup:
        response.update(decision=setup['direction'], reason='range_reversal_'+setup['direction'].lower(),
                        stop_distance=setup['stop_distance'], target_distance=setup['target_distance'])
    assessment = assess_market(market, max_spread_points=settings.max_spread_points)
    if settings.market_state_policy_enabled:
        apply_market_policy(response, assessment)
    return response, assessment


def payoff(bars, times, *, quote_time, bid, ask, side, stop_distance, target_distance,
           point, horizon=4, cost_r=0.0):
    """Ignore pre-entry extremes; SL-first on ambiguity, adverse gap at open.

    Entry is the recorded executable quote. Only the subsequent full M15 bars
    are observed. Movement inside the entry bucket is deliberately unknown.
    """
    if side not in {'BUY', 'SELL'} or min(stop_distance, target_distance, point) <= 0:
        return None
    start = (quote_time//900+1)*900
    index = bisect_left(times, start)
    future = bars[index:index+horizon]
    if len(future) != horizon or any(row['time'] != start+i*900 for i,row in enumerate(future)):
        return None
    if any(row['spread_points'] <= 0 for row in future):
        return None
    entry = ask if side=='BUY' else bid
    sign = 1 if side=='BUY' else -1
    stop, target = entry-sign*stop_distance, entry+sign*target_distance
    exit_price, kind, closed = 0.0, 'TIME', future[-1]['time']+900
    for row in future:
        offset = row['spread_points']*point if side=='SELL' else 0.0
        op, high, low = (row[k]+offset for k in ('open','high','low'))
        hit_stop = low<=stop if side=='BUY' else high>=stop
        hit_target = high>=target if side=='BUY' else low<=target
        if hit_stop:
            exit_price = min(stop,op) if side=='BUY' else max(stop,op)
            kind, closed = 'SL', row['time']+900
            break
        if hit_target:
            exit_price, kind, closed = target,'TP',row['time']+900
            break
    else:
        exit_price = future[-1]['close']+(future[-1]['spread_points']*point if side=='SELL' else 0.0)
    return {'r': sign*(exit_price-entry)/stop_distance-cost_r,
            'exit': kind, 'closed': closed}


def metrics(outcomes):
    equity = peak = drawdown = 0.0
    for r in outcomes:
        equity += r
        peak = max(peak,equity)
        drawdown = max(drawdown,peak-equity)
    return {'trades':len(outcomes),'wins':sum(r>0 for r in outcomes),
            'losses':sum(r<0 for r in outcomes),'net_r':round(equity,4),
            'mean_r':round(equity/len(outcomes),4) if outcomes else None,
            'max_drawdown_r':round(drawdown,4)}


def compare_rows(rows, bars, *, holdout_fraction=.2, cost_r=0.0):
    if not 0<holdout_fraction<1 or cost_r<0:
        raise ValueError('invalid comparison parameters')
    times = [r['time'] for r in bars]
    ordered = sorted(rows,key=lambda r:(r['quote_time'],r['sample_key']))
    # Split by unique signal bar, never split repeated snapshots across folds.
    buckets = sorted({r['signal_bar_time'] for r in ordered})
    cutoff = buckets[max(0,int(len(buckets)*(1-holdout_fraction)))] if buckets else None
    report = {'rows':len(rows),'unique_signal_bars':len(buckets),'holdout_start_broker':cutoff,
              'purge_seconds':4*900,'states':dict(Counter(r['assessment']['state'] for r in rows)),
              'decision_pairs':dict(Counter(r['base']['decision']+' → '+r['new']['decision'] for r in rows))}
    for name in ['development','holdout']:
        chosen = [r for r in ordered if cutoff is not None and
                  (r['signal_bar_time']<cutoff-4*900 if name=='development' else r['signal_bar_time']>=cutoff)]
        phase={'rows':len(chosen),'unique_signal_bars':len({r['signal_bar_time'] for r in chosen}),
               'blocked_winners':0,'blocked_losers':0,'blocked_unpriced':0,'blocked_flat':0}
        for version in ['base','new']:
            last_close=0;outcomes=[];unpriced=overlapping=0
            for r in chosen:
                d=r[version]
                if d['decision'] not in {'BUY','SELL'}:
                    continue
                if r['quote_time']<last_close:
                    overlapping+=1
                    continue
                result=payoff(bars,times,quote_time=r['quote_time'],bid=r['bid'],ask=r['ask'],
                              side=d['decision'],stop_distance=d['stop_distance'],
                              target_distance=d['target_distance'],point=r['point'],cost_r=cost_r)
                if not result:
                    unpriced+=1
                    if version=="base" and r["new"]["decision"]=="WAIT":
                        phase["blocked_unpriced"]+=1
                    continue
                last_close=result['closed'];outcomes.append(result['r'])
                # Count skipped opportunities only in the baseline's actual
                # non-overlapping sequence, not every 30s copy of one signal.
                if version=='base' and r['new']['decision']=='WAIT':
                    phase['blocked_winners' if result['r']>0 else 'blocked_losers' if result['r']<0 else 'blocked_flat']+=1
            phase[version]=metrics(outcomes)|{'unpriced':unpriced,'overlapping_skipped':overlapping}
        report[name]=phase
    report['promotion_eligible']=False
    report['limitations']=['recorded forecasts reused; not independent model generalization',
                          'M15 full bars omit entry-bucket and intrabar TP-stage/early-adverse behavior',
                          'news, broker lots, account loss locks and actual range quick target are not simulated',
                          'spread included; commission/slippage beyond adverse open gaps require cost stress',
                          'small number of distinct contexts cannot establish market-wide validity']
    return report


def analyze(db, *, symbol='XAUUSD_l', cost_r=0.0):
    path=Path(db).resolve()
    with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as con:
        con.row_factory=sqlite3.Row
        con.execute('PRAGMA query_only=ON');con.execute('BEGIN')
        bars=[dict(r) for r in con.execute("SELECT * FROM history_bars WHERE symbol=? AND timeframe='M15' ORDER BY time",(symbol,))]
        inputs=list(con.execute('SELECT a.sample_key,a.provenance_json,b.codec,b.body FROM inference_audit a JOIN input_blobs b ON a.input_sha256=b.sha256'))
        samples=[dict(r) for r in con.execute('SELECT * FROM decision_samples WHERE symbol=? ORDER BY quote_time,captured',(symbol,))]
    rows=[];skipped=Counter();settings=Settings()
    for stored in inputs:
        try:
            if stored['codec']!='zlib-json-v1':
                skipped['unsupported_input_codec']+=1;continue
            context=json.loads(zlib.decompress(stored['body']))
            provenance=json.loads(stored['provenance_json'])
            request=provenance['request']
            if request['symbol']!=symbol:
                continue
            payload={**context,**request}
            market=Market.from_dict(payload)
            f=provenance['forecast']
            forecast=RecordedForecast(Forecast(f['low'],f['median'],f['high'],tuple(f['median_path'])))
            if not market.micro_bars:
                skipped['missing_micro_context']+=1;continue
            quote=int(request['quote_time'])
            if not market.bars[-1].time+900<=quote<market.bars[-1].time+1800:
                skipped['quote_context_time_mismatch']+=1;continue
            # Range is not enabled counterfactually without historical execution
            # capability. Normal-entry comparison remains model-led and strict.
            base,_=route_recorded(market,forecast,replace(settings,market_state_policy_enabled=False),quote_time=quote)
            new,a=route_recorded(market,forecast,settings,quote_time=quote)
            rows.append({'sample_key':stored['sample_key'],'quote_time':quote,
                         'signal_bar_time':market.bars[-1].time,'bid':market.bid,'ask':market.ask,
                         'point':market.point,'base':base,'new':new,'assessment':a})
        except (ValueError,KeyError,TypeError,zlib.error):
            skipped['invalid_or_incomplete_recorded_input']+=1
    # Broad scenario coverage uses recorded quotes with past M15 history. It
    # cannot regenerate confirmation metrics or create new executable signals.
    coverage=Counter();coverage_skips=Counter();times=[r['time'] for r in bars]
    for row in samples:
        try:
            meta=json.loads(row['model_metadata'] or '{}');base=meta['decision_audit']['base']
            index=bisect_right(times,row['signal_bar_time'])
            if index<128 or times[index-1]!=row['signal_bar_time']:
                coverage_skips['missing_history_context']+=1;continue
            point=(base['signal_ask']-base['signal_bid'])/base['spread_points']
            m=Market(symbol,'M15',base['signal_bid'],base['signal_ask'],point,
                     tuple(Bar(**{k:b[k] for k in ('time','open','high','low','close')}) for b in bars[max(0,index-256):index]))
            m.validate();coverage[assess_market(m)['state']]+=1
        except (ValueError,KeyError,TypeError,ZeroDivisionError):
            coverage_skips['missing_audit_or_quote']+=1
    report=compare_rows(rows,bars,cost_r=cost_r)
    report.update(source_fingerprint=hashlib.sha256(json.dumps(bars,sort_keys=True).encode()+b''.join(
                      r['sample_key'].encode()+r['body']+r['provenance_json'].encode()
                      for r in sorted(inputs,key=lambda x:x['sample_key']))).hexdigest(),
                  schema_version=1,policy='market-state-v1',settings=asdict(settings),
                  recorded_inputs=len(inputs),skipped_inputs=dict(skipped),
                  coverage={'samples':len(samples),'classified':sum(coverage.values()),
                            'states':dict(coverage),'skipped':dict(coverage_skips)},
                  execution='offline_read_only',source_db=path.name,cost_r=cost_r)
    return report


def render_report(report):
    lines=['# ارزیابی تاریخی سیاست بازار رامون','',
           'این گزارش یک مقایسهٔ آفلاین با پیش‌بینی‌های ثبت‌شده است؛ نتیجهٔ واقعی اجرای اکسپرت یا تأیید سودآوری نیست.','',
           f"ورودی‌های کامل: {report['recorded_inputs']}؛ قابل بررسی: {report['rows']}؛ کندل‌های سیگنال متفاوت: {report['unique_signal_bars']}.",
           f"موارد حذف‌شده: `{json.dumps(report['skipped_inputs'],ensure_ascii=False)}`",'',
           '| بخش | سیاست | معاملهٔ غیرهم‌پوشان | خالص R | افت R | موارد فاقد قیمت‌گذاری |',
           '|---|---|---:|---:|---:|---:|']
    for phase in ['development','holdout']:
        for version in ['base','new']:
            m=report[phase][version]
            lines.append(f"| {phase} | {version} | {m['trades']} | {m['net_r']} | {m['max_drawdown_r']} | {m['unpriced']} |")
    lines+=['',f"زوج تصمیم‌ها: `{json.dumps(report['decision_pairs'],ensure_ascii=False)}`",'',
            f"پوشش وضعیت‌ها در تاریخچهٔ گسترده: {report['coverage']['classified']} از {report['coverage']['samples']} نمونه.",'',
            '| وضعیت | نمونه |','|---|---:|']
    lines += [f'| {k} | {v} |' for k,v in sorted(report['coverage']['states'].items())]
    lines+=['','آزمون نهایی بر اساس زمان کندل سیگنال جدا شده و ۴ کندل فاصلهٔ پاک‌سازی دارد. چند پاسخ یک کندل شواهد مستقل محسوب نمی‌شوند.','',
            'ورود از Bid/Ask ثبت‌شده، خروج SELL با Ask، برخورد هم‌زمان SL/TP با اولویت SL و gap زیان‌ده با قیمت بازشدن حساب می‌شود. فقط کندل‌های کامل بعد از بازهٔ ورود استفاده می‌شوند؛ هزینهٔ اضافی هر معامله در فایل JSON ثبت شده است.','',
            'در این مقایسه، مسیر رنج فعال نشده چون قابلیت اجرای تاریخی آن ثبت نشده است. نمودارهای دقیقه‌ای، مدیریت TP مرحله‌ای، خروج زودهنگام، خبر، لات کارگزار و قفل حساب به‌طور کامل بازسازی نشده‌اند.','',
            '**نتیجه: این داده به‌تنهایی اجازهٔ فعال‌سازی زنده نمی‌دهد.** نمونه‌های مستقل بیشتر و اجرای حساب آزمایشی لازم است.']
    return '\n'.join(lines)+'\n'


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db',required=True);parser.add_argument('--symbol',default='XAUUSD_l')
    parser.add_argument('--json-output',required=True);parser.add_argument('--report-output',required=True)
    parser.add_argument('--cost-r',type=float,default=0.0)
    args=parser.parse_args();report=analyze(args.db,symbol=args.symbol,cost_r=args.cost_r)
    Path(args.json_output).write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
    Path(args.report_output).write_text(render_report(report))
    print(json.dumps({k:report[k] for k in ['rows','unique_signal_bars','decision_pairs','development','holdout']},indent=2))


if __name__=='__main__':
    main()
