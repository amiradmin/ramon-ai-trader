#!/usr/bin/env python3
"""Generate order-free, explicitly confirmed first-use journal provisioning script.

Never run as recovery from a deleted/corrupt journal. Never overwrites a file.
Generation neither starts MT5 nor touches its common Files folder.
"""
from pathlib import Path
from generate_execution_journal_adapter import extract

ROOT = Path(__file__).resolve().parents[1]


def create_provisioner(source):
    scope = source[source.index('string V2StopoutScope()'):source.index('string V2StopoutReviewKey(')]
    core = extract(source, '// BEGIN V2 EXECUTION JOURNAL CORE', '// END V2 EXECUTION JOURNAL CORE')
    return HEADER + scope + core + PROVISION


HEADER = r'''
#property strict
#property script_show_inputs
input bool ConfirmFirstUseOnly=false;
input long ExpectedLogin=0;
input string ExpectedServer="";
bool AccountLockHealthy()
{
   return ConfirmFirstUseOnly && ExpectedLogin>0 && ExpectedServer!=""
      && AccountInfoInteger(ACCOUNT_LOGIN)==ExpectedLogin
      && AccountInfoString(ACCOUNT_SERVER)==ExpectedServer;
}
'''

PROVISION = r'''
void OnStart()
{
   V2ExecutionLease lease;
   if(!AccountLockHealthy() || !TerminalInfoInteger(TERMINAL_CONNECTED)
      || TerminalInfoInteger(TERMINAL_TRADE_ALLOWED) || PositionsTotal()!=0 || OrdersTotal()!=0)
   { Print("V2 PROVISION BLOCKED: explicit account confirmation, AlgoTrading OFF, connected and flat account required"); return; }
   V2ExecScope=V2StopoutScope();
   string path=V2ExecPath(V2ExecScope);
   if(V2ExecScope=="" || FileIsExist(path,FILE_COMMON))
   { Print("V2 PROVISION BLOCKED: invalid scope or journal already exists; never overwrite"); return; }
   V2ExecHandle=FileOpen(path,FILE_READ|FILE_WRITE|FILE_TXT|FILE_ANSI|FILE_COMMON,0,CP_UTF8);
   if(V2ExecHandle==INVALID_HANDLE || FileSize(V2ExecHandle)!=0 || !V2ExecIdentity()
      || PositionsTotal()!=0 || OrdersTotal()!=0)
   { Print("V2 PROVISION BLOCKED: lock/storage/account changed"); return; }
   if(!V2ExecAppend("RAMON_EXEC_V1|"+V2ExecScope))
   { Print("V2 PROVISION FAILED: storage error; operator review required; no deletion"); return; }
   Print("V2 PROVISION READY: ",path,"; no trading permission granted");
}
'''

if __name__ == '__main__':
    output = ROOT/'build/v2-execution-journal/RamonExecutionProvision.mq5'
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(create_provisioner((ROOT/'mt5/Ramon.mq5').read_text(encoding='utf-8-sig')))
    print(output)
