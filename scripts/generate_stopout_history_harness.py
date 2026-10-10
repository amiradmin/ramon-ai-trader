#!/usr/bin/env python3
"""Extract the exact production guard into an in-memory MQL fixture script.

All account, history and terminal-global operations are replaced by mocks.
No broker authentication, trade API, or real terminal-global writes are used.
"""
from generate_stopout_restart_probe import ROOT, PROBE, create_probe


def create_harness(source: str) -> str:
    base = create_probe(source)
    assert base.endswith(PROBE)
    base = base[:-len(PROBE)].replace('#property script_show_inputs\n', '')
    return '#property strict\n' + MOCKS + base + CASES


MOCKS = r'''
bool MockConnected=true,MockHistoryOK=true,MockReadOK=true,MockWriteOK=true;
bool MockSwitchServer=false;
long MockLogin=123;
string MockServer="FIXTURE-A";
ulong MockTickets[4];
long MockReasons[4],MockTimes[4];
int MockCount=0,MockSelectCount=0,MockFlushCount=0,MockGlobalsCount=0;
string MockNames[64];
double MockValues[64];
long MockAccountInfoInteger(ENUM_ACCOUNT_INFO_INTEGER property) { return MockLogin; }
string MockAccountInfoString(ENUM_ACCOUNT_INFO_STRING property) { return MockServer; }
long MockTerminalInfoInteger(ENUM_TERMINAL_INFO_INTEGER property) { return MockConnected ? 1 : 0; }
datetime MockTimeCurrent() { return (datetime)1800000000; }
bool MockHistorySelect(datetime start,datetime end)
{
   MockSelectCount++;
   if(MockSwitchServer) MockServer="FIXTURE-B";
   return MockHistoryOK;
}
int MockHistoryDealsTotal() { return MockCount; }
ulong MockHistoryDealGetTicket(int index) { return MockTickets[index]; }
bool MockHistoryDealGetInteger(ulong ticket,ENUM_DEAL_PROPERTY_INTEGER prop,long &value)
{
   if(!MockReadOK) return false;
   for(int i=0;i<MockCount;i++)
      if(MockTickets[i]==ticket)
      { value=(prop==DEAL_REASON ? MockReasons[i] : MockTimes[i]); return true; }
   return false;
}
int MockFind(string name)
{
   for(int i=0;i<MockGlobalsCount;i++) if(MockNames[i]==name) return i;
   return -1;
}
bool MockGlobalVariableCheck(string name) { return MockFind(name)>=0; }
bool MockGlobalVariableGet(string name,double &value)
{
   int i=MockFind(name);
   if(i<0) return false;
   value=MockValues[i]; return true;
}
datetime MockGlobalVariableSet(string name,double value)
{
   if(!MockWriteOK) return 0;
   int i=MockFind(name);
   if(i<0) { i=MockGlobalsCount++; MockNames[i]=name; }
   MockValues[i]=value; return (datetime)1800000000;
}
void MockGlobalVariablesFlush() { MockFlushCount++; }
#define AccountInfoInteger MockAccountInfoInteger
#define AccountInfoString MockAccountInfoString
#define TerminalInfoInteger MockTerminalInfoInteger
#define TimeCurrent MockTimeCurrent
#define HistorySelect MockHistorySelect
#define HistoryDealsTotal MockHistoryDealsTotal
#define HistoryDealGetTicket MockHistoryDealGetTicket
#define HistoryDealGetInteger MockHistoryDealGetInteger
#define GlobalVariableCheck MockGlobalVariableCheck
#define GlobalVariableGet MockGlobalVariableGet
#define GlobalVariableSet MockGlobalVariableSet
#define GlobalVariablesFlush MockGlobalVariablesFlush
'''

CASES = r'''
int Failures=0,Cases=0;
void ResetFixture()
{
   MockConnected=true; MockHistoryOK=true; MockReadOK=true; MockWriteOK=true;
   MockSwitchServer=false; MockLogin=123; MockServer="FIXTURE-A";
   MockCount=0; MockSelectCount=0; MockFlushCount=0; MockGlobalsCount=0;
   V2StopoutHistoryHealthy=false; V2StopoutFaultLatched=false;
}
void Stopout(int index,ulong ticket,long stamp)
{ MockTickets[index]=ticket; MockReasons[index]=DEAL_REASON_SO; MockTimes[index]=stamp; MockCount=index+1; }
void Check(bool ok,string name)
{
   Cases++;
   if(!ok) Failures++;
   Print("RAMON_HISTORY_FIXTURE ",ok?"PASS ":"FAIL ",name);
}
bool Veto(string expected)
{
   string reason="";
   bool allowed=V2Preflight(ORDER_TYPE_SELL,0,0,0,reason);
   return !allowed && reason==expected;
}
void OnStart()
{
   ResetFixture(); MockConnected=false;
   Check(Veto("terminal disconnected"),"disconnected blocks entry");
   ResetFixture(); MockHistoryOK=false;
   Check(Veto("stop-out history unavailable"),"history failure blocks entry");
   ResetFixture(); string reason="";
   Check(V2RecoverStopoutHistory(reason) && V2StopoutHistoryHealthy &&
      !GlobalVariableCheck(V2StopoutKey()),"empty new fixture scans without inventing Stop Out");
   ResetFixture(); Stopout(0,10,1700000000123);
   Check(Veto("stop-out lock") && MockFlushCount>0,"offline Stop Out recovers durable lock");
   ResetFixture(); Stopout(0,10,1700000000123);
   GlobalVariableSet(V2StopoutReviewKey(V2StopoutScope(),10),1700000000123.0);
   Check(V2RecoverStopoutHistory(reason) && !GlobalVariableCheck(V2StopoutKey()),
      "exact reviewed event does not recreate absent lock");
   Stopout(1,11,1700000000123); // Same millisecond, distinct ticket.
   Check(Veto("stop-out lock"),"new ticket at reviewed time still locks");
   ResetFixture(); Stopout(0,10,1700000000123);
   GlobalVariableSet(V2StopoutReviewKey(V2StopoutScope(),10),1700000000122.0);
   Check(Veto("stop-out lock"),"incorrect review timestamp does not waive Stop Out");
   ResetFixture(); Stopout(0,10,1700000000123);
   string old_scope=V2StopoutScope();
   GlobalVariableSet(V2StopoutReviewKey(old_scope,10),1700000000123.0);
   MockServer="FIXTURE-B";
   Check(V2StopoutScope()!=old_scope && Veto("stop-out lock"),"review cannot cross server");
   ResetFixture(); Stopout(0,10,1700000000123);
   old_scope=V2StopoutScope();
   GlobalVariableSet(V2StopoutReviewKey(old_scope,10),1700000000123.0);
   MockLogin=124;
   Check(V2StopoutScope()!=old_scope && Veto("stop-out lock"),"review cannot cross login");
   ResetFixture(); Stopout(0,10,1700000000123); MockReadOK=false;
   Check(Veto("stop-out history deal unreadable"),"unreadable deal blocks entry");
   ResetFixture(); Stopout(0,10,1700000000123); MockWriteOK=false;
   Check(Veto("stop-out lock persistence failed"),"write failure blocks entry");
   MockWriteOK=true; MockCount=0;
   Check(Veto("stop-out storage/event fault"),"write fault remains latched for session");
   ResetFixture(); MockWriteOK=false;
   Check(Veto("stop-out history checkpoint failed"),"checkpoint failure blocks entry");
   ResetFixture(); MockSwitchServer=true;
   Check(Veto("stop-out history account changed"),"account switch during scan blocks entry");
   ResetFixture();
   GlobalVariableSet(V2StopoutKey(),777.0);
   double stored=0;
   Check(Veto("stop-out lock") && GlobalVariableGet(V2StopoutKey(),stored) && stored==777.0,
      "existing legacy/test lock is preserved");
   ResetFixture(); MockCount=1; MockTickets[0]=10; MockReasons[0]=DEAL_REASON_CLIENT;
   Check(V2RecoverStopoutHistory(reason) && !GlobalVariableCheck(V2StopoutKey()),"non Stop Out deal does not lock");
   MockCount=0;
   Check(Veto("stop-out history shortened/invalid"),"truncated history blocks entry");
   ResetFixture(); Stopout(0,18446744073709551600,1700000000123);
   string review_key=V2StopoutReviewKey(V2StopoutScope(),MockTickets[0]);
   Check(StringLen(review_key)<=63 && StringFind(review_key,"18446744073709551600")>=0,
      "ulong tickets remain exact in bounded review keys");
   PrintFormat("RAMON_HISTORY_FIXTURE SUMMARY cases=%d failures=%d",Cases,Failures);
   // Isolated fixture terminal only; no broker/real global calls were used.
   TerminalClose(Failures==0 ? 0 : 1);
   return;
}
'''


if __name__ == '__main__':
    source = (ROOT/'mt5/Ramon.mq5').read_text(encoding='utf-8-sig')
    output = ROOT/'build/mt5-history-harness/RamonStopoutHistoryHarness.mq5'
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(create_harness(source))
    print(output)
