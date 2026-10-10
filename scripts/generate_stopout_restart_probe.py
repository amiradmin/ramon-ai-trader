#!/usr/bin/env python3
"""Build a demo-only, order-free restart probe using the production preflight."""
from pathlib import Path
import hashlib
import re

ROOT = Path(__file__).resolve().parents[1]


def create_probe(source: str) -> str:
    start = source.index('string V2StopoutKey()')
    end = source.index('\nvoid OnTradeTransaction(', start)
    guard = source[start:end]
    names = ('EnableV2AccountRiskGuard', 'V2MaxPortfolioRiskUSD',
             'V2MaxSymbolRiskUSD', 'V2MaxDirectionRiskUSD',
             'V2MinimumMarginLevelPercent', 'MoneyUnitsPerUSD')
    inputs = []
    for name in names:
        matches = re.findall(r'^input (?:bool|double) ' + name + r'\s*=\s*[^;]+;', source, re.M)
        if len(matches) != 1:
            raise ValueError(f'Missing or ambiguous input: {name}')
        inputs.append(matches[0])
    if 'bool V2Preflight(' not in guard:
        raise ValueError('Missing production preflight')
    output = ('#property strict\n#property script_show_inputs\n'
              '// Production guard SHA256: ' + hashlib.sha256(guard.encode()).hexdigest()
              + '\n' + '\n'.join(inputs) + '\n' + guard + PROBE)
    # Refuse future source changes that introduce an execution path.
    if re.search(r'\b(?:OrderSend\w*|CTrade)\b|\bTrade\s*\.|#include|#import', output):
        raise ValueError('Execution dependency found; refusing probe generation')
    return output


PROBE = r'''
// Run only in a disposable DEMO terminal, never Strategy Tester or real account.
input long ExpectedDemoLogin = 0;
input string ExpectedDemoServer = "";
input bool SeedLock = false; // false: read-only verification
input long ExpectedLockValue = 0; // copy exact value from SEED log

void OnStart()
{
   if((bool)MQLInfoInteger(MQL_TESTER) ||
      AccountInfoInteger(ACCOUNT_TRADE_MODE)!=ACCOUNT_TRADE_MODE_DEMO ||
      ExpectedDemoLogin<=0 || AccountInfoInteger(ACCOUNT_LOGIN)!=ExpectedDemoLogin ||
      ExpectedDemoServer=="" || AccountInfoString(ACCOUNT_SERVER)!=ExpectedDemoServer ||
      !TerminalInfoInteger(TERMINAL_CONNECTED))
   { Print("RAMON_RESTART REFUSED context/account mismatch"); return; }
   string key=V2StopoutKey();
   PrintFormat("RAMON_RESTART CONTEXT login=%I64d server=%s path=%s",
      ExpectedDemoLogin,ExpectedDemoServer,TerminalInfoString(TERMINAL_DATA_PATH));
   if(SeedLock)
   {
      // Never overwrite an existing lock; never delete a lock in this probe.
      if(GlobalVariableCheck(key))
      { Print("RAMON_RESTART REFUSED existing lock; preserve and review"); return; }
      long stamp=(long)TimeLocal();
      if(stamp<=0 || GlobalVariableSet(key,(double)stamp)==0)
      { Print("RAMON_RESTART FAIL lock write"); return; }
      GlobalVariablesFlush();
      if(!GlobalVariableCheck(key) || GlobalVariableGet(key)!=(double)stamp)
      { Print("RAMON_RESTART FAIL lock readback"); return; }
      PrintFormat("RAMON_RESTART SEED key=%s value=%I64d",key,stamp);
   }
   else if(ExpectedLockValue<=0 || !GlobalVariableCheck(key) ||
           GlobalVariableGet(key)!=(double)ExpectedLockValue)
   { Print("RAMON_RESTART FAIL lock absent or changed"); return; }
   string reason="";
   // Lock must reject before candidate geometry is evaluated. No order is sent.
   bool allowed=V2Preflight(ORDER_TYPE_SELL,0.0,0.0,0.0,reason);
   if(allowed || reason!="stop-out lock")
   { PrintFormat("RAMON_RESTART FAIL allowed=%s reason=%s",allowed?"true":"false",reason); return; }
   PrintFormat("RAMON_RESTART CHECK key=%s value=%.0f reason=%s seed=%s",
      key,GlobalVariableGet(key),reason,SeedLock?"true":"false");
   Print("RAMON_RESTART evidence only; process restart requires independent Journal review");
}
'''


if __name__ == '__main__':
    result = create_probe((ROOT / 'mt5/Ramon.mq5').read_text(encoding='utf-8-sig'))
    destination = ROOT / 'build/mt5-stopout-probe/RamonStopoutRestartProbe.mq5'
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(result, encoding='utf-8')
    print(destination)
