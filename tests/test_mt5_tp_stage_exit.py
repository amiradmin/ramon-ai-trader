"""Run the production MQL TP manager through a small terminal adapter."""
from pathlib import Path
import shutil
import re
import subprocess
import pytest


def test_tp_stage_requires_distinct_fresh_snapshots_and_keeps_price_exits(tmp_path):
    compiler=shutil.which('g++')
    if not compiler:
        pytest.skip('C++ adapter compiler unavailable')
    s=Path('mt5/Ramon.mq5').read_text()
    fn=s[s.index('bool ManageTPStages('):s.index('void ResetMainFastProfitState()')]
    timing = '\n'.join(re.search(r'const int ' + name + r' = \d+;', s).group() for name in ('ExitWeakSnapshotSpacingSeconds', 'ExitModelFreshnessSeconds'))
    timing += '\n' + s[s.index('void UpdateWeakConfirmation('):s.index('void ReadControlRiskCap()')]
    harness=r'''
#include <string>
#include <cmath>
#include <cassert>
using string=std::string;using datetime=long;
struct MqlTick { double bid=101.1,ask=101.2; };
string TPStageDirection="BUY",TPStageStatus,StatusLine,LastModelDecision="WAIT",detail;
int TPStage=1,TPStageWeakSnapshots=0,SnapshotIntervalSeconds=30,TPStageWeakSnapshotsRequired=2;
int TP1GraceSeconds=30,TP2GraceSeconds=30,MaxDeviationPoints=20;
datetime TPStageHitTime=1000,TPStageLastDecisionTime=1000,LastModelSnapshotTime=1030,now=1030,TPStageLastWeakCountTime=0;
double TPStageTP1=101,TPStageTP2=102,TPStageTP3=103,TPStageProgress=0;
double TP1RetraceFraction=.2,TP2RetraceFraction=.2,TP1HealthyProgressFraction=.35;
int POSITION_PRICE_OPEN=0,attempts=0;
bool supportive=false,paused=false,success=true;double price=101.1;
struct API { bool PositionClose(ulong,int){attempts++;return success;} ulong ResultDeal(){return 1;} } Trade;
bool LoadTPStagePlan(ulong){return true;}
bool SymbolInfoTick(string,MqlTick& t){t.bid=price;t.ask=price+.1;return true;}
string _Symbol="XAUUSD";
bool TargetReached(string d,double p,double target){return d=="BUY" ? p>=target : p<=target;}
datetime TimeCurrent(){return now;}
void MarkTPStageReached(int st,datetime n,ulong){TPStage=st;TPStageHitTime=n;TPStageWeakSnapshots=0;TPStageLastDecisionTime=LastModelSnapshotTime;}
void ProtectReachedTPStage(ulong){}
bool ManagedExitPausedForMarketClosed(ulong){return paused;}
void ResetMarketClosedExitPause(){}
void RecordDealTelemetry(ulong,string d){detail=d;}
void HandleManagedExitFailure(ulong,string){}
double PositionGetDouble(int){return 100;}
double DirectionalProgress(string d,double f,double t,double p){return (d=="BUY" ? p-f : f-p)/std::abs(t-f);}
bool TPStageModelSupportive(){return supportive;}
double MathAbs(double d){return std::abs(d);}
string DoubleToString(double,int){return "";}
string BoolText(bool){return "";}
template<class... T>void Print(T...){}
'''
    cases=r'''
void reset(){TPStage=1;TPStageWeakSnapshots=0;TPStageHitTime=1000;TPStageLastDecisionTime=1000;
 LastModelSnapshotTime=now=1030;TPStageLastWeakCountTime=0;LastModelDecision="WAIT";TPStageDirection="BUY";
 price=101.1;attempts=0;supportive=paused=false;success=true;detail="";}
int main(){
 reset();assert(!ManageTPStages(1));assert(TPStageWeakSnapshots==1);
 now=1035;assert(!ManageTPStages(1));assert(TPStageWeakSnapshots==1);
 now=LastModelSnapshotTime=1060;assert(ManageTPStages(1));assert(attempts==1);assert(detail=="tp1_stall_exit");
 reset();assert(!ManageTPStages(1));now=1200;assert(!ManageTPStages(1));assert(TPStageWeakSnapshots==0);
 reset();LastModelSnapshotTime=0;assert(!ManageTPStages(1));assert(TPStageWeakSnapshots==0);
 reset();LastModelSnapshotTime=999;assert(!ManageTPStages(1));
 reset();LastModelSnapshotTime=1100;assert(!ManageTPStages(1)); // future
 reset();LastModelDecision="INVALID";assert(!ManageTPStages(1));
 reset();assert(!ManageTPStages(1));now=LastModelSnapshotTime=1060;supportive=true;assert(!ManageTPStages(1));assert(TPStageWeakSnapshots==0);
 reset();LastModelSnapshotTime=0;price=100.7;assert(ManageTPStages(1));assert(attempts==1); // price retrace independent of model
 reset();price=103.;assert(ManageTPStages(1));assert(detail=="tp3_stage_exit");
 reset();assert(!ManageTPStages(1));now=LastModelSnapshotTime=1060;paused=true;assert(ManageTPStages(1));assert(attempts==0);
 paused=false;assert(ManageTPStages(1));assert(attempts==1);
 reset();TPStageDirection="SELL";TPStageTP1=99;TPStageTP2=98;TPStageTP3=97;price=98.8;
 assert(!ManageTPStages(1));now=LastModelSnapshotTime=1060;assert(ManageTPStages(1));assert(attempts==1);
}
'''
    cpp=tmp_path/'tp.cpp';cpp.write_text(harness+timing+fn+cases)
    binary=tmp_path/'tp'
    subprocess.run([compiler,'-std=c++17',str(cpp),'-o',str(binary)],check=True,capture_output=True,text=True)
    subprocess.run([str(binary)],check=True,capture_output=True,text=True)


def test_main_maximum_hold_boundary_and_failed_close(tmp_path):
    compiler=shutil.which('g++')
    if not compiler:
        pytest.skip('C++ adapter compiler unavailable')
    s=Path('mt5/Ramon.mq5').read_text()
    fn=s[s.index('bool ManageMainMaximumHold('):s.index('void ManageOpenPosition()')]
    code=r'''
#include <string>
#include <cassert>
using string=std::string;using datetime=long;
string _Symbol="XAUUSD",StatusLine,detail;int PERIOD_M15=15,MaximumHoldBars=4,MaxDeviationPoints=20;
int age=3,attempts=0,failures=0;bool paused=false,success=true;
int iBarShift(string,int,datetime,bool){return age;}
bool ManagedExitPausedForMarketClosed(ulong){return paused;}
void ResetMarketClosedExitPause(){}
void RecordDealTelemetry(ulong,string d){detail=d;}
void HandleManagedExitFailure(ulong,string){failures++;}
struct API{bool PositionClose(ulong,int){attempts++;return success;}ulong ResultDeal(){return 1;}}Trade;
'''
    cases=r'''
int main(){assert(!ManageMainMaximumHold(1,100));assert(attempts==0);
 age=-1;assert(!ManageMainMaximumHold(1,100));age=4;
 paused=true;assert(ManageMainMaximumHold(1,100));assert(attempts==0);
 paused=false;success=false;assert(ManageMainMaximumHold(1,100));assert(attempts==1 && failures==1 && detail.empty());
 success=true;assert(ManageMainMaximumHold(1,100));assert(detail=="maximum_hold_bars");
}
'''
    cpp=tmp_path/'hold.cpp';cpp.write_text(code+fn+cases)
    binary=tmp_path/'hold'
    subprocess.run([compiler,'-std=c++17',str(cpp),'-o',str(binary)],check=True,capture_output=True,text=True)
    subprocess.run([str(binary)],check=True,capture_output=True,text=True)
