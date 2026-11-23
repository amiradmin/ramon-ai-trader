"""Compare MT5 read-only snapshot against historical replay assumptions.

Does not silently change historical simulation inputs or approve live trading.
"""
import argparse
import json
from math import isclose, isfinite
from pathlib import Path

FIELDS={"point":"point","trade_tick_size":"tick_size",
        "trade_tick_value":"tick_value","volume_min":"volume_min",
        "volume_max":"volume_max","volume_step":"volume_step"}


def compare(snapshot,report):
    if snapshot.get("source")!="MT5_CONNECTED_TERMINAL_READ_ONLY":
        raise ValueError("Invalid broker snapshot origin")
    if report.get("mode")!="ISOLATED_READ_ONLY_LABORATORY":
        raise ValueError("Invalid laboratory report")
    if snapshot.get("symbol")!=report.get("symbol"):
        raise ValueError("Symbol mismatch: cannot compare different contracts")
    assumed=report.get("assumptions") or {}
    fields=snapshot.get("broker_fields") or {}
    diffs=[]
    for actual,expected in FIELDS.items():
        a=fields.get(actual)
        b=assumed.get(expected)
        if a is None or b is None or not all(isfinite(float(x)) for x in (a,b)):
            diffs.append({"field":expected,"status":"MISSING_OR_INVALID","mt5":a,"lab":b})
        else:
            diffs.append({"field":expected,"status":"MATCH" if isclose(float(a),float(b),rel_tol=1e-6,abs_tol=1e-9) else "MISMATCH",
                          "mt5":a,"lab":b})
    balance=snapshot.get("balance")
    b=assumed.get("balance")
    balance_comparable=(balance is not None and b is not None and
                        isclose(float(balance),float(b),rel_tol=1e-6,abs_tol=0.01))
    reasons=[]
    if not balance_comparable:
        reasons.append("SNAPSHOT_BALANCE_DIFFERS_FROM_HISTORICAL_REPLAY_BALANCE")
    reasons.append("CENT_ACCOUNT_DENOMINATION_AND_TICK_VALUE_CURRENCY_NOT_INDEPENDENTLY_VERIFIED")
    reasons.append("CURRENT_MT5_SNAPSHOT_DOES_NOT_VERIFY_HISTORICAL_CONTRACT_OR_SPREADS")
    return {"status":"BROKER_SNAPSHOT_COMPARED_NOT_LIVE_APPROVED",
            "symbol":report.get("symbol"),"currency_reported_by_mt5":snapshot.get("currency"),
            "fields":diffs,"mismatches":sum(x["status"]!="MATCH" for x in diffs),
            "mt5_balance_snapshot":balance,"lab_starting_balance":b,
            "balance_matches_snapshot":balance_comparable,"warnings":reasons,
            "live_eligible":False}


def main():
    p=argparse.ArgumentParser(description="Compare broker snapshot to lab assumptions (offline)")
    p.add_argument("broker_snapshot_json")
    p.add_argument("lab_report_json")
    args=p.parse_args()
    snapshot=json.loads(Path(args.broker_snapshot_json).read_text(encoding="utf-8"))
    report=json.loads(Path(args.lab_report_json).read_text(encoding="utf-8"))
    print(json.dumps(compare(snapshot,report),indent=2))


if __name__=="__main__":
    main()
