"""Portable, read-only long-history laboratory metrics and chronological partitions."""
from datetime import date, datetime, timezone
from hashlib import sha256
from math import isfinite


def partition_dates(start, end):
    """Use fixed calendar cutoffs; the test set is never used for parameter selection."""
    if start > end:
        raise ValueError("Invalid historical range")
    # Dates chosen before looking at strategy outcomes.
    first = date(2025, 7, 1)
    second = date(2026, 4, 1)
    ranges = [
        ("TRAIN", start, min(end, first.fromordinal(first.toordinal()-1))),
        ("VALIDATION", max(start,first), min(end, second.fromordinal(second.toordinal()-1))),
        ("TEST_UNTOUCHED", max(start,second), end),
    ]
    return [{"name":name,"start":lo.isoformat(),"end":hi.isoformat()}
            for name,lo,hi in ranges if lo<=hi]


def fingerprint(rows):
    digest=sha256()
    for row in rows:
        digest.update(("{time}:{open:.8f}:{high:.8f}:{low:.8f}:{close:.8f};".format(
            time=row["time"],open=row["open"],high=row["high"],
            low=row["low"],close=row["close"])).encode("ascii"))
    return digest.hexdigest()


def period_metrics(trades, initial_balance):
    """Closed-trade cash P&L grouped by entry period, not independent broker fills."""
    equity=float(initial_balance)
    peak=equity
    dd=0.0
    pnl_values=[]
    crosses=0
    uncertain=0
    for trade in sorted(trades,key=lambda x:(x["closed_at"],x["opened_at"])):
        pnl=float(trade["pnl"])
        if not isfinite(pnl):
            raise ValueError("Non-finite trade P&L")
        pnl_values.append(pnl)
        equity+=pnl
        peak=max(peak,equity)
        if peak>0:
            dd=max(dd,100*(peak-equity)/peak)
        if trade.get("data_gap_affected") or trade.get("gaps_crossed",0):
            uncertain+=1
    wins=[v for v in pnl_values if v>0]
    losses=[v for v in pnl_values if v<0]
    gross_win=sum(wins)
    gross_loss=-sum(losses)
    return {
        "closed_trades":len(pnl_values),
        "wins":len(wins),
        "win_rate":round(100*len(wins)/len(pnl_values),2) if pnl_values else None,
        "net_gain":round(sum(pnl_values),2),
        "profit_factor":round(gross_win/gross_loss,3) if gross_loss>0 else None,
        "max_closed_trade_drawdown_pct":round(dd,2),
        "gap_affected_trades":uncertain,
        "expectancy_per_trade":round(sum(pnl_values)/len(pnl_values),4) if pnl_values else None
    }


def breakdown(report, start, end, initial_balance, *, name):
    results=[]
    for part in partition_dates(start,end):
        lo=date.fromisoformat(part["start"])
        hi=date.fromisoformat(part["end"])
        subset=[]
        boundary_crossings=0
        for trade in report["trades"]:
            opened=datetime.fromtimestamp(trade["opened_at"],tz=timezone.utc).date()
            closed=datetime.fromtimestamp(trade["closed_at"],tz=timezone.utc).date()
            if lo<=opened<=hi:
                subset.append(trade)
                if closed>hi:
                    boundary_crossings+=1
        results.append({**part,**period_metrics(subset,initial_balance),
                        "boundary_crossing_trades":boundary_crossings})
    return {"name":name,"periods":results}


def quality_gate(quality, baseline, candidate, *, min_test_trades=30):
    """Never call incomplete historical evidence 'live ready'."""
    reasons=[]
    if quality.get("gap_count",0):
        reasons.append("GAPS_IN_STORED_M15_HISTORY")
    if any(quality.get("excluded",{}).values()):
        reasons.append("EXCLUDED_OR_MISALIGNED_CANDLES")
    if candidate.get("status") not in ("DESCRIPTIVE_ONLY",):
        reasons.append("CANDIDATE_REPLAY_NOT_COMPLETE")
    if baseline.get("open_position") or candidate.get("open_position"):
        reasons.append("OPEN_POSITION_AT_REPLAY_END")
    train_or_test=next((p for p in candidate.get("periods",[])
                        if p["name"]=="TEST_UNTOUCHED"),None)
    if not train_or_test or train_or_test["closed_trades"]<min_test_trades:
        reasons.append("INSUFFICIENT_UNTOUCHED_TEST_TRADES")
    return {"status":"DESCRIPTIVE_NOT_APPROVED_FOR_LIVE",
            "live_eligible":False,"warnings":reasons,
            "reason":"Historical replay alone never authorizes live trading"}
