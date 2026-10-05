"""Execute the EA's shared entry gates and range protocol with terminal adapters."""
from pathlib import Path
import shutil
import subprocess

import pytest


def test_all_entry_routes_obey_execution_gates_and_range_protocol(tmp_path):
    source = Path('mt5/Ramon.mq5').read_text()
    timer = source.split('void OnTimer()', 1)[1]
    news = timer[timer.index('   if(NewsGuardEntryBlocked())'):timer.index('   // Automatic Ramon entries')]
    limits = timer[timer.index('   string account_loss_reason="";'):timer.index('   if(stop_distance<=0.0')]
    reason = source[source.index('bool RangeMainReasonValid('):source.index('bool RangeMainRewardRiskValid(')]
    # Return statements in OnTimer stop execution; adapt them to a result for the harness.
    gates = (news + limits).replace('return;', 'return false;')
    adapter = r'''
#include <string>
#include <cassert>
using string=std::string;
struct MqlTick {long time;};
string _Symbol="XAUUSD",StatusLine;
bool news=false,loss=false,cooldown=false,quote_ok=true;
int today_count=0,spread=42,MaxTradesPerDay=400,MaxSpreadPoints=50;
long now=1000,quote_at=1000;
const int SYMBOL_SPREAD=1;
void ShowStatus(){}
bool NewsGuardEntryBlocked(){return news;}
bool AccountLossLimitsBlocked(string &why){why="account loss";return loss;}
bool LocalLossCooldownBlocked(string,string &why){why="loss cooldown";return cooldown;}
int TradesToday(){return today_count;}
bool SymbolInfoTick(string,MqlTick &tick){tick.time=quote_at;return quote_ok;}
long TimeCurrent(){return now;}
long SymbolInfoInteger(string,int){return spread;}
'''
    cases = r'''
void reset(){news=loss=cooldown=false;quote_ok=true;today_count=0;spread=42;now=quote_at=1000;StatusLine="";}
int main(){
 // Route 0 is automatic, 1 is manual analytical pass, 2 is dashboard selection.
 for(int route=0;route<3;route++) {
  bool dashboard=route==2;
  reset();assert(allowed(dashboard,false,"BUY"));assert(allowed(dashboard,false,"SELL"));
  news=true;assert(!allowed(dashboard,false,"BUY"));reset();
  loss=true;assert(!allowed(dashboard,false,"BUY"));reset();
  cooldown=true;assert(!allowed(dashboard,false,"SELL"));reset();
  today_count=399;assert(allowed(dashboard,false,"BUY"));
  today_count=400;assert(!allowed(dashboard,false,"BUY"));
  today_count=-1;assert(!allowed(dashboard,false,"BUY"));reset();
  spread=51;assert(!allowed(dashboard,false,"BUY"));reset();
  quote_ok=false;assert(!allowed(dashboard,false,"BUY"));reset();
  quote_at=969;assert(!allowed(dashboard,false,"BUY"));
 }
 assert(RangeMainReasonValid("BUY","range_reversal_buy"));
 assert(RangeMainReasonValid("SELL","range_reversal_sell"));
 assert(RangeMainReasonValid("BUY","manual_override_range_pass"));
 assert(RangeMainReasonValid("SELL","manual_override_range_pass"));
 assert(!RangeMainReasonValid("BUY","range_reversal_sell"));
 assert(!RangeMainReasonValid("SELL","range_reversal_buy"));
 assert(!RangeMainReasonValid("WAIT","manual_override_range_pass"));
 assert(!RangeMainReasonValid("BUY","manual_override_final_pass"));
}
'''
    compiler = shutil.which('g++')
    if not compiler:
        pytest.skip('C++ adapter compiler unavailable')
    cpp = tmp_path / 'entry_routes.cpp'
    cpp.write_text(adapter + reason + '\nbool allowed(bool dashboard_manual_entry,bool SmallOnlyMode,string decision){\n' + gates + '\nreturn true;\n}\n' + cases)
    binary = tmp_path / 'entry_routes'
    subprocess.run([compiler, '-std=c++17', str(cpp), '-o', str(binary)], check=True, capture_output=True, text=True)
    subprocess.run([str(binary)], check=True, capture_output=True, text=True)
