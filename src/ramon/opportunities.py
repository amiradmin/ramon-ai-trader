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
        live = audit.get("direction_models", {})
        quality = live.get("direction_quality", {}) if isinstance(live, dict) else {}
        if not quality:
            # Historical compatibility for decisions saved before the live schema.
            legacy = audit.get("shadow_forecasts", {})
            quality = legacy.get("direction_quality", {}) if isinstance(legacy, dict) else {}
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


def _market_trade_scenarios(market_state, ai_side, buy_probability, sell_probability):
    """Return practical manual trade choices for the observed market state.

    These are decision-support scenarios, not automatic authorizations.
    """
    state = str(market_state or "UNCERTAIN").upper()
    scenarios = []

    def add(code, label, direction, setup, risk="normal"):
        if direction not in {"BUY", "SELL"}:
            return
        probability = buy_probability if direction == "BUY" else sell_probability
        key = (code, direction)
        if any((row["code"], row["direction"]) == key for row in scenarios):
            return
        scenarios.append({
            "code": code,
            "label": label,
            "direction": direction,
            "setup": setup,
            "risk": risk,
            "probability": probability,
        })

    if ai_side in {"BUY", "SELL"}:
        add("AI_DIRECTION", "انتخاب Direction AI", ai_side, "جهت پیشنهادی مدل")

    if state == "TREND_UP":
        add("TREND_CONTINUATION", "ادامه روند", "BUY", "همراه روند صعودی")
        add("PULLBACK_ENTRY", "خرید روی پولبک", "BUY", "ورود بعد از اصلاح")
        add("COUNTERTREND_REVERSAL", "برگشت خلاف روند", "SELL", "فقط با نشانه برگشت", "high")
    elif state == "TREND_DOWN":
        add("TREND_CONTINUATION", "ادامه روند", "SELL", "همراه روند نزولی")
        add("PULLBACK_ENTRY", "فروش روی پولبک", "SELL", "ورود بعد از اصلاح")
        add("COUNTERTREND_REVERSAL", "برگشت خلاف روند", "BUY", "فقط با نشانه برگشت", "high")
    elif state == "PULLBACK_UP":
        add("PULLBACK_ENTRY", "پایان پولبک و خرید", "BUY", "بازگشت به روند صعودی")
        add("PULLBACK_BREAK", "شکست پولبک", "SELL", "اگر اصلاح به برگشت تبدیل شود", "high")
    elif state == "PULLBACK_DOWN":
        add("PULLBACK_ENTRY", "پایان پولبک و فروش", "SELL", "بازگشت به روند نزولی")
        add("PULLBACK_BREAK", "شکست پولبک", "BUY", "اگر اصلاح به برگشت تبدیل شود", "high")
    elif state == "RANGE_LOW":
        add("RANGE_REVERSAL", "برگشت از کف رنج", "BUY", "خرید نزدیک کف")
        add("RANGE_BREAKOUT", "شکست کف رنج", "SELL", "فروش در شکست معتبر")
    elif state == "RANGE_HIGH":
        add("RANGE_REVERSAL", "برگشت از سقف رنج", "SELL", "فروش نزدیک سقف")
        add("RANGE_BREAKOUT", "شکست سقف رنج", "BUY", "خرید در شکست معتبر")
    elif state == "RANGE_MIDDLE":
        add("RANGE_WAIT_BUY", "خرید در لبه پایین", "BUY", "فعلاً صبر تا کف رنج", "high")
        add("RANGE_WAIT_SELL", "فروش در لبه بالا", "SELL", "فعلاً صبر تا سقف رنج", "high")
    elif state in {"BREAKOUT_UP", "BREAKOUT_RETEST_UP"}:
        add("BREAKOUT_CONTINUATION", "ادامه شکست", "BUY", "همراه شکست صعودی")
        add("FAILED_BREAKOUT", "شکست ناموفق", "SELL", "اگر قیمت دوباره زیر سطح برگردد", "high")
    elif state in {"BREAKOUT_DOWN", "BREAKOUT_RETEST_DOWN"}:
        add("BREAKOUT_CONTINUATION", "ادامه شکست", "SELL", "همراه شکست نزولی")
        add("FAILED_BREAKOUT", "شکست ناموفق", "BUY", "اگر قیمت دوباره بالای سطح برگردد", "high")
    elif state == "FALSE_BREAKOUT_UP":
        add("FALSE_BREAKOUT_REVERSAL", "برگشت بعد شکست کاذب", "SELL", "فروش پس از رد سقف")
        add("RECLAIM", "بازپس‌گیری شکست", "BUY", "اگر شکست دوباره تأیید شود", "high")
    elif state == "FALSE_BREAKOUT_DOWN":
        add("FALSE_BREAKOUT_REVERSAL", "برگشت بعد شکست کاذب", "BUY", "خرید پس از رد کف")
        add("RECLAIM", "بازپس‌گیری شکست", "SELL", "اگر شکست دوباره تأیید شود", "high")
    elif state in {"REGIME_TRANSITION", "CONFLICTING_STRUCTURE", "UNCERTAIN"}:
        add("DISCRETIONARY_BUY", "خرید دستی", "BUY", "ساختار نامشخص؛ فقط با بررسی خودت", "high")
        add("DISCRETIONARY_SELL", "فروش دستی", "SELL", "ساختار نامشخص؛ فقط با بررسی خودت", "high")
    elif state in {"VOLATILITY_SHOCK", "DISORDERLY_MARKET", "PRICE_GAP", "LOW_LIQUIDITY"}:
        add("HIGH_RISK_BUY", "خرید پرریسک", "BUY", "شرایط غیرعادی", "very_high")
        add("HIGH_RISK_SELL", "فروش پرریسک", "SELL", "شرایط غیرعادی", "very_high")

    return scenarios


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
                external=audit.get('external_models',{}) if isinstance(audit.get('external_models'),dict) else {}
                moment=external.get('moment',{}) if isinstance(external.get('moment'),dict) else {}
                finbert=external.get('finbert',{}) if isinstance(external.get('finbert'),dict) else {}
                news=audit.get('news_snapshot',{}) if isinstance(audit.get('news_snapshot'),dict) else {}
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
                    'ai_score':final.get('ai_engine_v2_score'),
                    'entry_probability':final.get('ai_engine_v2_entry_probability',final.get('entry_probability')),
                    'full_sl_probability':final.get('ai_engine_v2_full_sl_probability',final.get('full_sl_probability',final.get('shadow_full_sl_probability'))),
                    'market_state':final.get('market_state'),
                    'market_state_route':final.get('market_state_route'),
                    'trade_scenarios':_market_trade_scenarios(
                        final.get('market_state'), side, buy_probability, sell_probability
                    ),
                    'market_direction':final.get('market_direction'),
                    'market_direction_score':final.get('market_direction_score'),
                    'entry_timing_ready':final.get('entry_timing_ready'),
                    'entry_timing_direction':final.get('entry_timing_direction'),
                    'intrabar_confirmed':base.get('intrabar_confirmed'),
                    'intrabar_direction':base.get('intrabar_direction'),
                    'intrabar_move_atr':base.get('intrabar_move_atr'),
                    'ai_trend_confirmed':base.get('ai_trend_confirmed'),
                    'ai_trend_direction':base.get('ai_trend_direction'),
                    'ai_trend_score':base.get('ai_trend_score'),
                    'moment_ratio':moment.get('moment_anomaly_ratio'),
                    'moment_label':moment.get('moment_anomaly_label'),
                    'finbert_label':finbert.get('finbert_sentiment_label'),
                    'finbert_score':finbert.get('finbert_directional_score'),
                    'news_title':news.get('news_event_title'),
                    'news_impact':news.get('news_event_impact'),
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
                'scope':'Decision desk shows all practical market-state scenarios. Manual table execution may override analytical Ramon gates, while hard broker/account/risk/quote safety remains active.'}
    except sqlite3.Error as exc:
        return {'opportunities':[], 'error':str(exc),'scope':'positive_model_edge'}
    finally:con.close()
