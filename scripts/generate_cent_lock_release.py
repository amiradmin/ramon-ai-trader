#!/usr/bin/env python3
"""Generate a bounded, operator-confirmed release script for a *test* V2 lock.

The generated MQL5 script has no trading APIs.  It can remove only one exact
test lock after verifying the account, server, disabled Algo Trading, a flat
account, and the absence of broker-reported Stop Out deals.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "build" / "mt5-cent-lock-release"
CONFIRMATION_PREFIX = "RELEASE_TEST_LOCK_"


def create_release(login: int, server: str, lock_value: int) -> str:
    if login <= 0 or lock_value <= 0 or not re.fullmatch(r"[A-Za-z0-9_. -]+", server):
        raise ValueError("Explicit valid account/server/test-lock value required")
    confirmation = f"{CONFIRMATION_PREFIX}{login}"
    return RELEASE_SCRIPT.replace("@LOGIN@", str(login)).replace(
        "@SERVER@", server
    ).replace("@LOCK_VALUE@", str(lock_value)).replace("@CONFIRMATION@", confirmation)


RELEASE_SCRIPT = r'''#property strict
#property script_show_inputs

// Generated for one exact, previously verified test lock.  This script has no
// order, position-close, or trade-management API and refuses all other states.
input string OperatorConfirmation = "";

void OnStart()
{
   const long expected_login=@LOGIN@;
   const string expected_server="@SERVER@";
   const long expected_lock_value=@LOCK_VALUE@;
   const string required_confirmation="@CONFIRMATION@";
   Print("RAMON_TEST_LOCK_RELEASE START no-order release");

   for(int i=0;i<60 && !IsStopped();i++)
   {
      if(TerminalInfoInteger(TERMINAL_CONNECTED) &&
         AccountInfoInteger(ACCOUNT_LOGIN)==expected_login &&
         AccountInfoString(ACCOUNT_SERVER)==expected_server) break;
      Sleep(500);
   }
   if((bool)MQLInfoInteger(MQL_TESTER) ||
      AccountInfoInteger(ACCOUNT_TRADE_MODE)!=ACCOUNT_TRADE_MODE_REAL ||
      AccountInfoInteger(ACCOUNT_LOGIN)!=expected_login ||
      AccountInfoString(ACCOUNT_SERVER)!=expected_server ||
      !TerminalInfoInteger(TERMINAL_CONNECTED) ||
      TerminalInfoInteger(TERMINAL_TRADE_ALLOWED) ||
      OperatorConfirmation!=required_confirmation)
   { Print("RAMON_TEST_LOCK_RELEASE REFUSED identity/connection/algo/confirmation"); return; }
   if(PositionsTotal()!=0 || OrdersTotal()!=0)
   { PrintFormat("RAMON_TEST_LOCK_RELEASE REFUSED positions=%d orders=%d",PositionsTotal(),OrdersTotal()); return; }

   // A genuine broker Stop Out must be investigated through the approved review
   // workflow; this release path is intentionally limited to the known test lock.
   if(!HistorySelect(0,TimeCurrent()))
   { Print("RAMON_TEST_LOCK_RELEASE REFUSED history unavailable"); return; }
   for(int i=0;i<HistoryDealsTotal();i++)
   {
      ulong deal=HistoryDealGetTicket(i);
      long reason=0;
      if(deal==0 || !HistoryDealGetInteger(deal,DEAL_REASON,reason))
      { Print("RAMON_TEST_LOCK_RELEASE REFUSED unreadable history"); return; }
      if(reason==DEAL_REASON_SO)
      { PrintFormat("RAMON_TEST_LOCK_RELEASE REFUSED broker stop-out deal=%I64u",deal); return; }
   }

   string key="RAMON_V2_STOP_OUT_"+IntegerToString(expected_login);
   if(!GlobalVariableCheck(key) || GlobalVariableGet(key)!=(double)expected_lock_value)
   { Print("RAMON_TEST_LOCK_RELEASE REFUSED lock missing/changed"); return; }
   PrintFormat("RAMON_TEST_LOCK_RELEASE VERIFIED login=%I64d server=%s key=%s value=%I64d positions=0 orders=0 algo=false",
      expected_login,expected_server,key,expected_lock_value);
   if(!GlobalVariableDel(key))
   { Print("RAMON_TEST_LOCK_RELEASE FAIL delete"); return; }
   GlobalVariablesFlush();
   if(GlobalVariableCheck(key))
   { Print("RAMON_TEST_LOCK_RELEASE FAIL lock still present"); return; }
   Print("RAMON_TEST_LOCK_RELEASE PASS test lock removed; keep Algo Trading off and run live-start preflight before arming");
}
'''


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--login", required=True, type=int)
    parser.add_argument("--server", required=True)
    parser.add_argument("--lock-value", required=True, type=int)
    args = parser.parse_args()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_DIR / "RamonTestLockRelease.mq5"
    path.write_text(create_release(args.login, args.server, args.lock_value), encoding="utf-8")
    print(path)


if __name__ == "__main__":
    main()
