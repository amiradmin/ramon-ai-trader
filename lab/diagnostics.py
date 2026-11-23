"""Read-only data quality, account-unit and trade-cohort diagnostics.

No broker integration. Gap classification is heuristic and never asserts a
precise market calendar; externally verified sessions require separate data.
"""
from collections import Counter
from datetime import datetime, timezone
from math import isfinite
from statistics import median


def gap_diagnostics(rows, *, expected_seconds=900):
    totals=Counter()
    samples={}
    missing_slots=Counter()
    for a,b in zip(rows,rows[1:]):
        delta=int(b["time"])-int(a["time"])
        if delta==expected_seconds:
            continue
        start=datetime.fromtimestamp(a["time"],timezone.utc)
        end=datetime.fromtimestamp(b["time"],timezone.utc)
        # Friday into Sunday/Monday across the broker's usual closed interval
        # is a likely session closure, NOT proof of a valid trading calendar.
        calendar_weekend=(delta>=24*3600 and (
            (start.weekday()==4 and end.weekday()>=6) or
            (start.weekday()>=5 and end.weekday() in (0,6))))
        if delta<=0 or delta%expected_seconds:
            kind="IRREGULAR_TIMESTAMP"
        elif calendar_weekend:
            kind="LIKELY_WEEKEND_CLOSURE"
        elif delta>=48*3600:
            kind="MULTIDAY_CLOSURE_UNVERIFIED"
        elif delta>=6*3600:
            kind="LONG_SESSION_OR_HOLIDAY_UNVERIFIED"
        else:
            kind="UNEXPECTED_INTRADAY_GAP"
        totals[kind]+=1
        missing_slots[kind]+=max(0, delta//expected_seconds-1)
        if len(samples.setdefault(kind,[]))<5:
            samples[kind].append({
                "previous_utc":start.isoformat(),
                "next_utc":end.isoformat(),
                "elapsed_hours":round(delta/3600,2),
                "missing_15m_slots":max(0,delta//expected_seconds-1)})
    return {
        "method":"UTC_WEEKEND_HEURISTIC_NOT_BROKER_SESSION_CALENDAR",
        "total_discontinuities":sum(totals.values()),
        "categories":dict(sorted(totals.items())),
        "estimated_missing_slots":dict(sorted(missing_slots.items())),
        "examples":samples,
        "verified_market_calendar":False,
        "note":"Weekend and holiday classifications are provisional; every gap must be treated as unknown price path in replay."
    }


def risk_diagnostics(candidate):
    rows=candidate.get("risk_rejections",[])
    if not rows:
        return {"count":0, "records":[],
                "summary":None,
                "note":"No minimum-lot risk rejections recorded"}
    keys=("minimum_lot_risk","minimum_lot_risk_percent",
          "minimum_required_balance","stop_points","affordable_volume_unrounded")
    summary={}
    for k in keys:
        values=sorted(float(row[k]) for row in rows if row.get(k) is not None)
        if values:
            summary[k]={"min":round(values[0],4),
                        "median":round(median(values),4),
                        "max":round(values[-1],4)}
    return {"count":len(rows),"summary":summary,"records":rows,
            "note":"All values are calculated using caller-provided tick value/size and account units; not broker-verified. Rejection does not mean profitable opportunity."}


def contract_diagnostics(args):
    # Input checks, NOT an assertion that LiteFinance contract specs are true.
    suspicious=[]
    for field in ("balance","point","tick_size","tick_value",
                  "volume_min","volume_max","volume_step"):
        value=float(getattr(args,field))
        if not isfinite(value) or value<=0:
            suspicious.append("INVALID_"+field.upper())
    if args.volume_min>args.volume_max:
        suspicious.append("VOLUME_MIN_EXCEEDS_MAX")
    if args.tick_size<args.point:
        suspicious.append("TICK_SIZE_BELOW_POINT_CHECK_BROKER")
    steps=(args.volume_max-args.volume_min)/args.volume_step
    if abs(steps-round(steps))>1e-6:
        suspicious.append("VOLUME_RANGE_NOT_ALIGNED_WITH_STEP")
    return {
        "status":"UNVERIFIED_BROKER_CONTRACT",
        "verified":False,
        "account_currency":"UNVERIFIED",
        "tick_value_currency":"UNVERIFIED",
        "account_units":"UNVERIFIED_MAY_BE_CENTS",
        "warnings":suspicious or ["MANUALLY_VERIFY_CENT_ACCOUNT_TICK_VALUE_AND_BALANCE_UNITS"],
        "formula":"risk_money = abs(entry-stop) * (tick_value/tick_size) * lots",
        "broker_checklist":[
            "MT5 symbol_info trade_tick_size and trade_tick_value",
            "MT5 volume_min, volume_max and volume_step",
            "MT5 account_info currency and trade account cent denomination",
            "Broker contract size, tick value profit/loss and historical conversion",
            "Measured bid-ask spreads, commissions and slippage distribution"
        ],
        "no_live_execution_authorized":True
    }


def per_year(report, *, initial_balance):
    from segmented import period_metrics
    groups={}
    for trade in report.get("trades",[]):
        year=str(datetime.fromtimestamp(trade["opened_at"],timezone.utc).year)
        groups.setdefault(year,[]).append(trade)
    return {year:period_metrics(trades,initial_balance)
            for year,trades in sorted(groups.items())}


def per_regime(report, *, initial_balance):
    from segmented import period_metrics
    groups={}
    for trade in report.get("trades",[]):
        regime=str(trade.get("signal_regime") or "UNCLASSIFIED")
        groups.setdefault(regime,[]).append(trade)
    return {key:period_metrics(trades,initial_balance)
            for key,trades in sorted(groups.items())}


def sensitivity_metric(report):
    return {k:report.get(k) for k in (
        "signals","closed_trades","win_rate","net_gain",
        "profit_factor","max_drawdown_pct","open_position","status")}
