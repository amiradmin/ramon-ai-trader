#!/usr/bin/env python3
"""Explicitly bound live-Cent lock probe, with no trading API or lock removal.

Only for an operator-authorized order-free test. Does not simulate Stop Out.
"""
import argparse
from pathlib import Path
import re

from generate_stopout_restart_probe import ROOT, PROBE, create_probe


def create_cent_probe(source: str, login: int, server: str, stamp: int, seed: bool) -> str:
    if login <= 0 or stamp <= 0 or not re.fullmatch(r'[A-Za-z0-9_. -]+', server):
        raise ValueError('Explicit valid account/server/lock value required')
    base = create_probe(source)
    assert base.endswith(PROBE)
    base = base[:-len(PROBE)].replace('#property script_show_inputs\n', '')
    return base + CENT_PROBE.replace('@LOGIN@', str(login)).replace('@SERVER@', server).replace(
        '@STAMP@', str(stamp)).replace('@SEED@', 'true' if seed else 'false')


CENT_PROBE = r'''
// User explicitly authorized this no-order lock test on the live Cent account.
// No EAs, DLLs, order APIs or automatic lock deletion are present.
void OnStart()
{
   const long expected_login=@LOGIN@;
   const string expected_server="@SERVER@";
   const long stamp=@STAMP@;
   const bool seed=@SEED@;
   Print("RAMON_CENT_LOCK START no-order probe");
   // Startup scripts can run before account synchronization.
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
      TerminalInfoInteger(TERMINAL_TRADE_ALLOWED))
   { Print("RAMON_CENT_LOCK REFUSED account/connection/trading switch"); return; }
   // Refuse to restart a terminal with any exposure needing management.
   if(PositionsTotal()!=0 || OrdersTotal()!=0)
   { PrintFormat("RAMON_CENT_LOCK REFUSED positions=%d orders=%d",PositionsTotal(),OrdersTotal()); return; }
   PrintFormat("RAMON_CENT_LOCK CONTEXT login=%I64d server=%s currency=%s path=%s positions=%d orders=%d algo=false",
      expected_login,expected_server,AccountInfoString(ACCOUNT_CURRENCY),
      TerminalInfoString(TERMINAL_DATA_PATH),PositionsTotal(),OrdersTotal());
   string key=V2StopoutKey();
   if(seed)
   {
      if(GlobalVariableCheck(key))
      { Print("RAMON_CENT_LOCK REFUSED existing lock preserved"); return; }
      if(GlobalVariableSet(key,(double)stamp)==0)
      { Print("RAMON_CENT_LOCK FAIL write"); return; }
      GlobalVariablesFlush();
   }
   if(!GlobalVariableCheck(key) || GlobalVariableGet(key)!=(double)stamp)
   { Print("RAMON_CENT_LOCK FAIL missing/changed value"); return; }
   string reason="";
   bool allowed=V2Preflight(ORDER_TYPE_SELL,0.0,0.0,0.0,reason);
   if(allowed || reason!="stop-out lock")
   { PrintFormat("RAMON_CENT_LOCK FAIL reason=%s",reason); return; }
   PrintFormat("RAMON_CENT_LOCK CHECK phase=%s key=%s value=%I64d reason=%s",
      seed?"SEED":"VERIFY",key,stamp,reason);
   // Clean process exit only while still flat. Never close positions/orders.
   if(PositionsTotal()!=0 || OrdersTotal()!=0)
   { Print("RAMON_CENT_LOCK REFUSED terminal exit: exposure changed"); return; }
   Print("RAMON_CENT_LOCK requesting clean terminal exit; lock retained");
   TerminalClose(0);
   return;
}
'''


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--login', type=int, required=True)
    parser.add_argument('--server', required=True)
    parser.add_argument('--lock-value', type=int, required=True)
    parser.add_argument('--phase', choices=['seed', 'verify'], required=True)
    args = parser.parse_args()
    result = create_cent_probe((ROOT/'mt5/Ramon.mq5').read_text(encoding='utf-8-sig'),
                              args.login, args.server, args.lock_value, args.phase == 'seed')
    path = ROOT/'build/mt5-cent-probe'/f'RamonCentLock{args.phase.title()}.mq5'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(result, encoding='utf-8')
    print(path)
