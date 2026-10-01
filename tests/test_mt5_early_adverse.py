"""Exercise the actual MQL adverse-exit function with a terminal API adapter."""
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).parents[1]


def test_early_adverse_exit_against_position_and_snapshot_cases(tmp_path):
    compiler = shutil.which("g++")
    if not compiler:
        pytest.skip("C++ compiler required for MQL API adapter")
    source = (ROOT / "mt5/Ramon.mq5").read_text()
    functions = source[source.index("void ResetEarlyAdverseState()"):
                       source.index("void SelectDynamicProfitProtectionThresholds(")]
    harness = r'''
#include <string>
#include <cmath>
#include <cassert>
using string=std::string;
using datetime=long;
enum { POSITION_PROFIT, POSITION_TYPE, POSITION_TYPE_BUY, POSITION_TYPE_SELL };
const bool EnableEarlyAdverseExit=true;
const double EarlyAdverseRiskFraction=.60,SmallEarlyAdverseRiskFraction=.50;
const int EarlyAdverseWeakSnapshotsRequired=2,EarlyAdverseMinAgeSeconds=120;
const int SnapshotIntervalSeconds=30,MaxDeviationPoints=20;
ulong EarlyAdverseTicket=0;
double EarlyAdverseInitialRiskUnits=0,EarlyAdverseTriggerLossUnits=0;
double EarlyAdverseAppliedRiskFraction=0;
int EarlyAdverseWeakSnapshots=0;
datetime EarlyAdverseLastDecisionTime=0;
bool EarlyAdverseTriggered=false;
datetime LastModelSnapshotTime=0;
string LastModelDecision="WAIT",LastIntrabarDirection="NONE",LastAiTrendDirection="NONE";
bool LastIntrabarConfirmed=false,LastAiTrendConfirmed=false;
string StatusLine,detail;
bool small=false,paused=false,close_ok=true,readable=true;
long side=POSITION_TYPE_BUY,clock_now=1200;
double risk=10,profit=-6;
int attempts=0,recorded=0,failures=0;
struct TradeAPI {
 bool PositionClose(ulong,int){attempts++;return close_ok;}
 ulong ResultDeal(){return 1;}
} Trade;
bool PositionSelectByTicket(ulong){return readable;}
double ManagedPositionInitialRiskUnits(ulong){return risk;}
bool IsSmallProfitPosition(ulong){return small;}
double SmallProfitRiskCapUnits(){return 8;}
double MathMin(double a,double b){return std::fmin(a,b);}
double MathMax(double a,double b){return std::fmax(a,b);}
datetime TimeCurrent(){return clock_now;}
double PositionGetDouble(int){return profit;}
long PositionGetInteger(int){return side;}
string DoubleToString(double v,int){return std::to_string(v);}
string IntegerToString(int v){return std::to_string(v);}
string BoolText(bool b){return b ? "true" : "false";}
template<class... T> void Print(T...){ }
bool ManagedExitPausedForMarketClosed(ulong){return paused;}
void ResetMarketClosedExitPause(){ }
void RecordDealTelemetry(ulong,string s){recorded++;detail=s;}
void HandleManagedExitFailure(ulong,string){failures++;}
'''
    cases = r'''
void reset(){
 ResetEarlyAdverseState();
 small=paused=false;close_ok=readable=true;
 side=POSITION_TYPE_BUY;clock_now=1200;risk=10;profit=-6;
 LastModelSnapshotTime=1200;LastModelDecision="WAIT";
 LastIntrabarConfirmed=LastAiTrendConfirmed=false;
 LastIntrabarDirection=LastAiTrendDirection="NONE";
 attempts=recorded=failures=0;detail="";
}
bool check(ulong ticket=1){return ManageEarlyAdverseExit(ticket,1000);}
void next(){clock_now+=30;LastModelSnapshotTime=clock_now;}
int main(){
 reset();assert(!check());assert(EarlyAdverseWeakSnapshots==1);
 assert(!check());assert(EarlyAdverseWeakSnapshots==1); // same response
 next();assert(check());assert(attempts==1 && recorded==1);
 assert(detail=="early_adverse_exit");
 reset();side=POSITION_TYPE_SELL;assert(!check());next();assert(check());
 reset();profit=-5.99;assert(!check());assert(EarlyAdverseWeakSnapshots==0);
 reset();clock_now=1119;LastModelSnapshotTime=1119;assert(!check());
 clock_now=1120;LastModelSnapshotTime=1120;assert(!check());
 assert(EarlyAdverseWeakSnapshots==1); // exact minimum age
 reset();LastModelDecision="BUY";assert(!check());next();assert(!check());
 reset();LastIntrabarConfirmed=true;LastIntrabarDirection="BUY";
 assert(!check());next();assert(!check());assert(attempts==0);
 reset();LastAiTrendConfirmed=true;LastAiTrendDirection="BUY";
 assert(!check());next();assert(!check());assert(attempts==0);
 reset();assert(!check());next();LastModelDecision="BUY";assert(!check());
 assert(EarlyAdverseWeakSnapshots==0);next();LastModelDecision="WAIT";
 assert(!check());next();assert(check());
 reset();assert(!check());clock_now=1300;assert(!check());
 assert(EarlyAdverseWeakSnapshots==0); // stale response breaks streak
 LastModelSnapshotTime=1300;assert(!check());next();assert(check());
 reset();LastModelSnapshotTime=0;assert(!check());assert(attempts==0);
 reset();LastModelSnapshotTime=999;assert(!check()); // before position entry
 reset();LastModelDecision="INVALID";assert(!check());
 reset();assert(!check());next();profit=-2;assert(!check());
 assert(EarlyAdverseWeakSnapshots==0); // recovery breaks streak
 reset();assert(!check());next();assert(!check(2)); // new ticket resets streak
 assert(EarlyAdverseWeakSnapshots==1 && attempts==0);
 reset();risk=0;assert(!check());assert(attempts==0);
 reset();small=true;profit=-4;assert(!check());
 assert(EarlyAdverseTriggerLossUnits==4);next();assert(check());
 reset();assert(!check());next();paused=true;assert(check());
 assert(attempts==0 && recorded==0);paused=false;assert(check());
 assert(attempts==1 && recorded==1);
 reset();assert(!check());next();close_ok=false;assert(check());
 assert(attempts==1 && recorded==0 && failures==1);
 close_ok=true;assert(check());assert(recorded==1);
}
'''
    adapter = tmp_path / "adverse.cpp"
    adapter.write_text(harness + functions + cases)
    binary = tmp_path / "adverse"
    subprocess.run([compiler, "-std=c++17", str(adapter), "-o", str(binary)],
                   check=True, capture_output=True, text=True)
    subprocess.run([str(binary)], check=True, capture_output=True, text=True)


def test_only_valid_model_responses_publish_snapshot_time():
    source = (ROOT / "mt5/Ramon.mq5").read_text()
    timer = source.split("void OnTimer()", 1)[1]
    valid = timer.index("LastModelSnapshotTime=LastDecisionRequestTime;")
    assert valid > timer.index('StatusLine="Invalid/stale model response"')
    assert timer[:valid].count("LastModelSnapshotTime=0; EarlyAdverseWeakSnapshots=0;") == 2
