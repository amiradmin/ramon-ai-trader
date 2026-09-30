"""Execute real MQL intrabar direction, pattern, debounce and coordination logic."""
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import pytest
from ramon.history import persist_trade_outcome

EA=Path(__file__).parents[1]/'mt5/Ramon.mq5'


def compile_run(tmp_path, functions, harness, cases):
    compiler=shutil.which('g++')
    if not compiler: pytest.skip('g++ required for MQL adapter')
    functions=re.sub(r'const MqlRates &(\w+)\[\]',r'const std::vector<MqlRates> &\1',functions)
    cpp=tmp_path/'live.cpp';cpp.write_text(harness+functions+cases)
    binary=tmp_path/'live'
    result=subprocess.run([compiler,'-std=c++17',str(cpp),'-o',str(binary)],capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    result=subprocess.run([str(binary)],capture_output=True,text=True)
    assert result.returncode==0,result.stderr


BASE=r'''
#include <cassert>
#include <cmath>
#include <algorithm>
#include <vector>
#include <string>
#include <limits>
using string=std::string;using datetime=long;
struct MqlRates{datetime time;double open,high,low,close;};
struct MqlTick{datetime time;double bid,ask;long time_msc;};
#define ArraySize(x) ((int)x.size())
#define MathMin std::min
#define MathMax std::max
#define MathIsValidNumber std::isfinite
'''


def test_live_direction_and_forming_m1_breakout(tmp_path):
    s=EA.read_text();fn=s[s.index('bool LiveBarsValid('):s.index('string PrimaryPriorityKey(')]
    cases=r'''
int main(){datetime now=180020;
 std::vector<MqlRates> f={{180000,100,106,98,105},{179700,100,104,98,103},{179400,100,104,98,102}};
 std::vector<MqlRates> q={{180000,100,106,98,105},{179100,100,104,98,103},{178200,100,104,98,102}};
 MqlTick tick={now,105,105.4,now*1000};
 assert(LivePriceDirection(f,q,tick,now,10)=="BUY"); // current M5/M15 still open
 tick.bid=95;tick.ask=95.4;assert(LivePriceDirection(f,q,tick,now,10)=="SELL");
 tick.bid=100;tick.ask=100.4;assert(LivePriceDirection(f,q,tick,now,10)=="NONE");
 tick.bid=105;tick.ask=105.4;tick.time=now-6;assert(LivePriceDirection(f,q,tick,now,10)=="NONE");
 tick.time=now+1;assert(LivePriceDirection(f,q,tick,now,10)=="NONE");
 tick.time=now;f[1].time--;assert(LivePriceDirection(f,q,tick,now,10)=="NONE");f[1].time++;
 tick.bid=std::numeric_limits<double>::infinity();assert(LivePriceDirection(f,q,tick,now,10)=="NONE");
 tick.bid=105;
 std::vector<MqlRates> m={{180000,101,106,100,105},{179940,102,103,99,101},
 {179880,103,104,100,102},{179820,102,104,100,103}};
 string d;double anchor;
 assert(IntrabarPullbackPattern(m,tick,now,10,"BUY",d,anchor)&&d=="BUY"&&anchor==99);
 assert(!IntrabarPullbackPattern(m,tick,now,10,"SELL",d,anchor));
 tick.bid=106;tick.ask=106.4;assert(!IntrabarPullbackPattern(m,tick,now,10,"BUY",d,anchor)); // chase limit
 tick.bid=102.9;tick.ask=103.3;assert(!IntrabarPullbackPattern(m,tick,now,10,"BUY",d,anchor));
 tick.bid=105;tick.ask=105.4;
 for(auto &b:m){double h=b.high;b.open=300-b.open;b.close=300-b.close;b.high=300-b.low;b.low=300-h;}
 tick.bid=194.6;tick.ask=195;
 assert(IntrabarPullbackPattern(m,tick,now,10,"SELL",d,anchor)&&d=="SELL"&&anchor==201);
 m[0].time=179940;assert(!IntrabarPullbackPattern(m,tick,now,10,"SELL",d,anchor));
}
'''
    compile_run(tmp_path,fn,BASE,cases)


def test_distinct_quote_debounce_reset_and_staleness(tmp_path):
    s=EA.read_text();struct=s[s.index('struct LiveEvidence'):s.index('LiveEvidence TrendEvidence')]
    fn=s[s.index('string AdvanceLiveEvidence('):s.index('bool LiveBarsValid(')]
    cases=r'''
int main(){LiveEvidence a{};
 assert(AdvanceLiveEvidence(a,"SELL",1,100,90,3)=="NONE");
 assert(AdvanceLiveEvidence(a,"SELL",1,105,90,3)=="NONE"&&a.count==1); // same quote
 assert(AdvanceLiveEvidence(a,"SELL",2,105,90,3)=="NONE"&&a.count==2);
 assert(AdvanceLiveEvidence(a,"SELL",3,106,90,3)=="NONE"&&a.count==2); // too soon
 assert(AdvanceLiveEvidence(a,"SELL",4,110,90,3)=="SELL");
 assert(AdvanceLiveEvidence(a,"BUY",5,111,90,3)=="NONE"&&a.count==1); // reversal
 assert(AdvanceLiveEvidence(a,"NONE",6,112,90,3)=="NONE"&&a.count==0);
 assert(AdvanceLiveEvidence(a,"BUY",7,113,90,2)=="NONE");
 assert(AdvanceLiveEvidence(a,"BUY",8,118,90,2)=="BUY");
 assert(AdvanceLiveEvidence(a,"BUY",9,119,180,2)=="NONE"); // new bar
 assert(AdvanceLiveEvidence(a,"BUY",10,140,180,2)=="NONE"&&a.count==1); // gap
 assert(AdvanceLiveEvidence(a,"BUY",11,139,180,2)=="NONE"&&a.count==1); // clock rewind
}
'''
    compile_run(tmp_path,struct+fn,BASE,cases)


def test_primary_priority_and_signal_fallback_execute_real_mql(tmp_path):
    s=EA.read_text()
    priority=s[s.index('bool PrimaryPriorityBlocks('):s.index('void AppendLiveMonitorCsv(')]
    candidate=s[s.index('bool PullbackCandidate('):s.index('bool IsPullbackPosition(')]
    harness=BASE+r'''
#include <map>
using ulong=unsigned long;
std::map<string,double> globals;bool has_position=false;
string _Symbol="XAUUSD_l";const ulong PrimaryMagicNumber=12;
const int POSITION_SYMBOL=1,POSITION_MAGIC=2;
int PositionsTotal(){return has_position?1:0;}ulong PositionGetTicket(int){return 1;}
string PositionGetString(int){return _Symbol;}long PositionGetInteger(int){return 12;}
string PrimaryPriorityKey(string suffix){return suffix;}
bool GlobalVariableCheck(string key){return globals.count(key);}
double GlobalVariableGet(string key){return globals[key];}
datetime TimeCurrent(){return 100;}
bool SmallOnlyMode=true,EnableSmallProfitTrades=true,LastIntrabarConfirmed=false,LastAiTrendConfirmed=false;
string LiveMonitorStatus="INTRABAR_SETUP_CONFIRMED",LiveSetupDirection="SELL";
string LastIntrabarDirection="BUY",LastAiTrendDirection="BUY";
double LiveSetupAnchor=102;datetime LiveMonitorQuoteTime=100;
'''
    cases=r'''
int main(){string d,reason;double anchor;
 assert(!PullbackCandidate("BUY","forecast_up",d,reason,anchor)); // missing MAIN heartbeat
 globals["AT"]=100;globals["READY"]=0;
 assert(PullbackCandidate("BUY","forecast_up",d,reason,anchor)&&d=="SELL"&&anchor==102); // MAIN blocked
 globals["READY"]=1;assert(!PullbackCandidate("BUY","forecast_up",d,reason,anchor));
 globals["READY"]=0;has_position=true;assert(!PullbackCandidate("WAIT","insufficient_model_strength",d,reason,anchor));
 has_position=false;globals["AT"]=84;assert(!PullbackCandidate("BUY","forecast_up",d,reason,anchor));
 globals["AT"]=100;assert(!PullbackCandidate("WAIT","news_event_risk",d,reason,anchor));
 LastIntrabarConfirmed=true;assert(!PullbackCandidate("BUY","forecast_up",d,reason,anchor));
 LastIntrabarConfirmed=false;LiveMonitorQuoteTime=94;assert(!PullbackCandidate("BUY","forecast_up",d,reason,anchor));
}
'''
    compile_run(tmp_path,priority+candidate,harness,cases)


def test_intrabar_training_exclusion_survives_retry(tmp_path):
    db=tmp_path/'history.sqlite3'
    p=dict(trade_key='s:1:3',sample_key='b'*16,symbol='XAUUSD_l',direction='SELL',
        opened=1800010000,closed=1800010900,net_units=3.,initial_risk_units=2.,
        exit_reason='DEAL_REASON_TP',entry_strategy='intrabar_trend_pullback')
    persist_trade_outcome(db,p,1800011000);del p['entry_strategy']
    persist_trade_outcome(db,p,1800011001)
    with sqlite3.connect(db) as c:
        assert c.execute('SELECT entry_strategy,training_status FROM trade_outcomes').fetchone()==(
            'intrabar_trend_pullback','CENSORED_ALTERNATIVE_STRATEGY')


def test_cached_entry_and_tick_callback_keep_execution_guards():
    s=EA.read_text();tick=s.split('void OnTick()',1)[1].split('void OnTimer()',1)[0]
    assert 'UpdateLiveMonitor()' in tick
    assert 'Trade.' not in tick and 'QueryModel' not in tick
    entry=s.split('void TryLiveEntry()',1)[1].split('void OnTradeTransaction(',1)[0]
    assert 'bar_time!=LastSignalBarTime' in entry and '!LastModelSnapshotValid' in entry
    assert 'GetTickCount64()-MonitorLastQuoteClock>5000' in entry
    assert entry.index('LIVE TREND VETO:')<entry.index('Trade.Buy(')
    assert 'stop_loss_units>SmallProfitRiskCapUnits()+0.00001' in entry
    assert entry.index('PrimaryPriorityBlocks(shared_risk_reason)')<entry.index('Trade.Buy(')
