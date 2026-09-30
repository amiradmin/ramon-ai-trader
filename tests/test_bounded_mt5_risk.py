"""Execute actual MQL risk helpers through a minimal C++ calculation adapter."""
from pathlib import Path
import shutil
import subprocess

import pytest


def test_actual_mql_budget_override_and_volume_rounding(tmp_path):
    compiler = shutil.which("g++")
    if not compiler:
        pytest.skip("g++ required for MQL adapter")
    source = (Path(__file__).parents[1] / "mt5/Ramon.mq5").read_text()
    bounds = source[source.index("double BoundedEntryRiskUSD("):source.index("bool MinimumLotOverrideEligible()")]
    sizing = source[source.index("double SelectVolume("):source.index("void ClearPendingSizing()")]
    harness = r'''
#include <algorithm>
#include <cmath>
#include <cassert>
using ENUM_ORDER_TYPE=int;
const char* _Symbol="XAUUSD_l";
enum {SYMBOL_VOLUME_MIN, SYMBOL_VOLUME_MAX, SYMBOL_VOLUME_STEP};
double MoneyUnitsPerUSD=100,MaxExecutableRiskUSD=.20,MaxMinLotBudgetMultiple=1.25;
bool AllowMinLotRiskOverride=true;
double MathMin(double a,double b){return std::min(a,b);}
double MathFloor(double a){return std::floor(a);}
double NormalizeDouble(double a,int n){double p=std::pow(10,n);return std::round(a*p)/p;}
double EffectiveRiskPerTradeUSD(){return .06;}
double SymbolInfoDouble(const char*,int p){return p==SYMBOL_VOLUME_MAX?10.:.01;}
bool readable=true;
bool OrderCalcProfit(int side,const char*,double volume,double entry,double stop,double& out){
 out=volume*100*(side==0?stop-entry:entry-stop);return readable;
}
'''
    cases = r'''
int main(){
 assert(std::abs(BoundedEntryRiskUSD(.06,true)-.075)<1e-10);
 assert(BoundedEntryRiskUSD(.19,true)==.20);
 assert(BoundedEntryRiskUSD(-1,true)==0);
 assert(BoundedEntryRiskUSD(.06,false)==.06);
 assert(SelectVolume(0,100,93.2)==.01); // 6.8c minimum inside the bounded exception
 assert(SelectVolume(0,100,92.49)==0); // 7.51c minimum rejected
 assert(SelectVolume(1,100,107.51)==0); // SELL symmetric
 assert(std::abs(SelectVolume(0,100,99)-.06)<1e-10); // rounds down to budget
 AllowMinLotRiskOverride=false;
 assert(SelectVolume(0,100,93.2)==0);
 readable=false;assert(SelectVolume(0,100,99)==0);
}
'''
    cpp = tmp_path / "risk.cpp"
    cpp.write_text(harness + bounds + sizing + cases)
    binary = tmp_path / "risk"
    subprocess.run([compiler,"-std=c++17",str(cpp),"-o",str(binary)],check=True,capture_output=True)
    subprocess.run([str(binary)],check=True,capture_output=True)


def test_actual_small_stop_reserves_slippage_inside_four_cent_cap(tmp_path):
    compiler = shutil.which("g++")
    if not compiler:
        pytest.skip("g++ required for MQL adapter")
    source = (Path(__file__).parents[1] / "mt5/Ramon.mq5").read_text()
    function = source[source.index("bool SmallProfitStop("):source.index("double SelectVolume(")]
    harness = r'''
#include <algorithm>
#include <cmath>
#include <cassert>
using ENUM_ORDER_TYPE=int;
enum {ORDER_TYPE_BUY,ORDER_TYPE_SELL,SYMBOL_POINT,SYMBOL_TRADE_TICK_SIZE,SYMBOL_TRADE_STOPS_LEVEL};
const char* _Symbol="XAUUSD_l"; int _Digits=2,MaxDeviationPoints=30;
struct MqlTick {double bid,ask;};
double MathMin(double a,double b){return std::min(a,b);}
double MathMax(double a,double b){return std::max(a,b);}
double MathAbs(double a){return std::abs(a);}
double MathFloor(double a){return std::floor(a);}
double NormalizeDouble(double a,int n){double p=std::pow(10,n);return std::round(a*p)/p;}
double SymbolInfoDouble(const char*,int){return .01;}
long minimum_stop=0;
long SymbolInfoInteger(const char*,int){return minimum_stop;}
double SmallProfitRiskCapUnits(){return 4.;}
bool OrderCalcProfit(int side,const char*,double volume,double entry,double exit,double& out){
 out=volume*100*(side==ORDER_TYPE_BUY?exit-entry:entry-exit);return true;
}
'''
    cases = r'''
int main(){
 MqlTick quote{100.,100.42};double stop=0,loss=0;
 assert(SmallProfitStop(ORDER_TYPE_BUY,100.42,90.,.01,quote,stop));
 OrderCalcProfit(ORDER_TYPE_BUY,_Symbol,.01,100.72,stop,loss);
 assert(-loss<=4.+1e-8); // worst permitted BUY entry still fits
 assert(SmallProfitStop(ORDER_TYPE_SELL,100.,110.,.01,quote,stop));
 OrderCalcProfit(ORDER_TYPE_SELL,_Symbol,.01,99.7,stop,loss);
 assert(-loss<=4.+1e-8); // symmetric SELL reservation
 minimum_stop=500;
 assert(!SmallProfitStop(ORDER_TYPE_BUY,100.42,90.,.01,quote,stop));
}
'''
    cpp = tmp_path / "small_stop.cpp"
    cpp.write_text(harness + function + cases)
    binary = tmp_path / "small_stop"
    subprocess.run([compiler,"-std=c++17",str(cpp),"-o",str(binary)],check=True,capture_output=True)
    subprocess.run([str(binary)],check=True,capture_output=True)
