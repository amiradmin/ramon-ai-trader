"""Read-only MT5 contract snapshot. Run only where MetaTrader5 Python API works.

Never logs in to a new account, sends orders, or exports identifiers.
Reads metadata from an ALREADY connected MT5 terminal.
"""
import argparse
import json
import math
import sys


def snapshot(mt5, symbol):
    account=mt5.account_info()
    info=mt5.symbol_info(symbol)
    if account is None:
        raise RuntimeError("No connected MT5 account; open your terminal first.")
    if info is None:
        raise RuntimeError("Symbol not found in connected terminal: "+symbol)
    fields=("point","trade_tick_size","trade_tick_value",
            "trade_tick_value_profit","trade_tick_value_loss",
            "trade_contract_size","volume_min","volume_max","volume_step",
            "trade_stops_level","trade_freeze_level")
    result={"source":"MT5_CONNECTED_TERMINAL_READ_ONLY",
            "symbol":symbol,"currency":str(account.currency),
            "balance":float(account.balance),
            "broker_fields":{k:float(getattr(info,k)) for k in fields},
            "note":"Account identifiers excluded. Broker metadata is a current snapshot, not historical contract evidence."}
    return result


def main():
    p=argparse.ArgumentParser(description="Read only symbol specification from connected MT5")
    p.add_argument("--symbol",default="XAUUSD_l")
    args=p.parse_args()
    try:
        import MetaTrader5 as mt5
    except ImportError as exc:
        raise SystemExit("MetaTrader5 Python API missing: run with Python configured for the MT5 terminal") from exc
    if not mt5.initialize():
        raise SystemExit("Could not initialize existing MT5 terminal: "+str(mt5.last_error()))
    try:
        result=snapshot(mt5,args.symbol)
        print(json.dumps(result,indent=2))
    finally:
        mt5.shutdown()


if __name__=="__main__":
    main()
