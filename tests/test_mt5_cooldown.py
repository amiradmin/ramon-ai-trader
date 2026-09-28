"""Execute the actual MQL cooldown functions with a C++ history API adapter.

This tests the algorithm, not MetaEditor compatibility or a real broker session.
Only MQL dynamic-array declarations are translated into std::vector.
"""
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).parents[1]


def test_terminal_cooldown_against_broker_history_cases(tmp_path):
    compiler = shutil.which('g++')
    if not compiler:
        pytest.skip('C++ compiler required for MQL history adapter')
    source = (ROOT / 'mt5/Ramon.mq5').read_text()
    functions = source[source.index('int ReadCooldownPosition('):source.index('void SyncClosedTrades()')]
    functions = functions.replace('ulong identifiers[];', 'std::vector<ulong> identifiers;')
    functions = functions.replace('long exit_times[];', 'std::vector<long> exit_times;')
    harness = r'''
#include <string>
#include <vector>
#include <set>
#include <algorithm>
#include <cmath>
#include <cassert>
#include <iostream>
using string=std::string;
using datetime=long;
const string _Symbol="XAUUSD_l";
const ulong MagicNumber=26092212;
enum { DEAL_SYMBOL, DEAL_PROFIT, DEAL_COMMISSION, DEAL_SWAP, DEAL_FEE,
 DEAL_TYPE, DEAL_ENTRY, DEAL_MAGIC, DEAL_VOLUME, DEAL_TIME_MSC, DEAL_TIME,
 DEAL_REASON, DEAL_POSITION_ID };
enum { DEAL_TYPE_BUY=100, DEAL_TYPE_SELL, DEAL_ENTRY_IN, DEAL_ENTRY_OUT,
 DEAL_ENTRY_OUT_BY, DEAL_ENTRY_INOUT, DEAL_REASON_SL, DEAL_REASON_CLIENT,
 DEAL_REASON_TP };
struct Deal {
 ulong ticket,id,magic; long at,type,entry,reason; double volume,profit,fee;
 string symbol="XAUUSD_l";
};
std::vector<Deal> all,selected;
std::set<ulong> opened;
long clock_now=2000;
bool readable=true,position_readable=true;
template<class T> int ArraySize(const std::vector<T>& a){return (int)a.size();}
template<class T> void ArrayResize(std::vector<T>& a,int n){a.resize(n);}
double MathAbs(double n){return std::abs(n);}
datetime TimeCurrent(){return clock_now;}
bool PositionIdOpen(ulong id){return opened.count(id);}
bool HistorySelect(long from,long to){
 selected.clear(); for(auto d:all) if(d.at>=from && d.at<=to) selected.push_back(d);
 return readable;
}
bool HistorySelectByPosition(ulong id){
 selected.clear(); for(auto d:all) if(d.id==id) selected.push_back(d);
 return position_readable;
}
int HistoryDealsTotal(){return selected.size();}
ulong HistoryDealGetTicket(int i){return selected.at(i).ticket;}
Deal lookup(ulong ticket){for(auto d:all) if(d.ticket==ticket) return d; std::abort();}
string HistoryDealGetString(ulong t,int field){return lookup(t).symbol;}
long HistoryDealGetInteger(ulong t,int f){auto d=lookup(t); switch(f){
 case DEAL_TYPE:return d.type; case DEAL_ENTRY:return d.entry;
 case DEAL_MAGIC:return d.magic; case DEAL_TIME:return d.at;
 case DEAL_TIME_MSC:return d.at*1000; case DEAL_REASON:return d.reason;
 case DEAL_POSITION_ID:return d.id; default:std::abort();}}
double HistoryDealGetDouble(ulong t,int f){auto d=lookup(t); switch(f){
 case DEAL_VOLUME:return d.volume; case DEAL_PROFIT:return d.profit;
 case DEAL_FEE:return d.fee; case DEAL_SWAP:case DEAL_COMMISSION:return 0;
 default:std::abort();}}
'''
    cases = r'''
void reset(){all.clear();opened.clear();clock_now=2000;readable=position_readable=true;}
void position(ulong id,long at,long side=DEAL_TYPE_BUY,long reason=DEAL_REASON_SL,
              double profit=-10,ulong magic=MagicNumber){
 all.push_back({id*10,id,magic,at-100,side,DEAL_ENTRY_IN,0,1,0,0});
 // Manual closing deals may have magic zero; ownership comes from opening deals.
 all.push_back({id*10+1,id,0,at,side==DEAL_TYPE_BUY?DEAL_TYPE_SELL:DEAL_TYPE_BUY,
                DEAL_ENTRY_OUT,reason,1,profit,0});
}
bool blocked(string side="BUY") {string reason; return LocalLossCooldownBlocked(side,reason);}
void pair(){reset();position(1,1000);position(2,1200);}
int main(){
 pair(); assert(blocked()); assert(!blocked("SELL")); // startup: no upload state
 clock_now=2999;assert(blocked());clock_now=3000;assert(!blocked());
 reset();position(1,1200);assert(!blocked()); // only one loss
 pair();position(3,1400,DEAL_TYPE_BUY,DEAL_REASON_CLIENT,5);assert(!blocked());
 pair();position(3,1400,DEAL_TYPE_BUY,DEAL_REASON_CLIENT,-5);assert(!blocked());
 pair();position(3,1400,DEAL_TYPE_BUY,DEAL_REASON_TP,10);assert(!blocked());
 pair();position(3,1400,DEAL_TYPE_SELL);assert(!blocked());
 pair();position(3,1400,DEAL_TYPE_BUY,DEAL_REASON_SL,1);assert(!blocked());
 pair();position(3,1400,DEAL_TYPE_BUY,DEAL_REASON_TP,10,123);assert(blocked());
 pair();position(3,1400,DEAL_TYPE_BUY,DEAL_REASON_TP,10);
 all[4].symbol=all[5].symbol="EURUSD_l";assert(blocked());
 // A partial close is not a closed position and cannot create a second loss.
 reset();position(1,1000);position(2,1200);all.back().volume=.5;opened.insert(2);
 assert(!blocked());
 // Multiple SL exit deals count as one position; a closed split exit is accepted.
 pair();all.back().volume=.5;
 all.push_back({22,2,0,1300,DEAL_TYPE_SELL,DEAL_ENTRY_OUT,DEAL_REASON_SL,.5,-5,0});
 assert(blocked());
 reset();position(2,1200);all.back().volume=.5;
 all.push_back({22,2,0,1300,DEAL_TYPE_SELL,DEAL_ENTRY_OUT,DEAL_REASON_SL,.5,-5,0});
 assert(!blocked());
 // Unsorted history still uses the final close time, not opening time or upload order.
 pair();position(3,1400,DEAL_TYPE_BUY,DEAL_REASON_CLIENT,5);
 std::reverse(all.begin(),all.end());assert(!blocked());
 pair();std::reverse(all.begin(),all.end());assert(blocked());
 pair();readable=false;string reason;
 assert(LocalLossCooldownBlocked("BUY",reason));assert(reason=="loss_cooldown_history_unavailable");
 pair();position_readable=false;assert(blocked());
 // Fees are part of the net-loss classification.
 reset();position(1,1000,DEAL_TYPE_BUY,DEAL_REASON_SL,1);all.back().fee=-2;
 position(2,1200,DEAL_TYPE_BUY,DEAL_REASON_SL,1);all.back().fee=-2;assert(blocked());
 reset();position(1,1000,DEAL_TYPE_SELL);position(2,1200,DEAL_TYPE_SELL);
 assert(blocked("SELL"));assert(!blocked("BUY"));
 std::cout << "MT5 cooldown history scenarios passed\n";
}
'''
    cpp = tmp_path / 'cooldown.cpp'
    cpp.write_text(harness + functions + cases)
    binary = tmp_path / 'cooldown'
    subprocess.run([compiler, '-std=c++17', '-Wall', '-Werror', str(cpp), '-o', str(binary)], check=True, capture_output=True, text=True)
    result = subprocess.run([str(binary)], check=True, capture_output=True, text=True)
    assert 'scenarios passed' in result.stdout


def test_execution_gate_precedes_order_and_does_not_gate_position_management():
    source = (ROOT / 'mt5/Ramon.mq5').read_text()
    timer = source[source.index('void OnTimer()'):]
    assert timer.index('ManageOpenPosition()') < timer.index('LocalLossCooldownBlocked(decision,cooldown_reason)')
    assert timer.index('LocalLossCooldownBlocked(decision,cooldown_reason)') < timer.index('Trade.Buy(')
    gate = source[source.index('int ReadCooldownPosition('):source.index('void SyncClosedTrades()')]
    assert 'WebRequest' not in gate
    assert 'ValidSampleKey' not in gate
    assert 'training_status' not in gate


def test_cooldown_is_never_called_by_display_functions():
    source = (ROOT / 'mt5/Ramon.mq5').read_text()
    call = 'LocalLossCooldownBlocked(decision,cooldown_reason)'
    assert source.count(call) == 1
    before_timer, timer = source.split('void OnTimer()', 1)
    assert call not in before_timer
    assert call in timer
    assert 'string cooldown_reason="";' not in before_timer
