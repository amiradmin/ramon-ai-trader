"""Compile and execute the actual account guard and actual quick-target RR guard."""
from pathlib import Path
import shutil
import subprocess
import pytest


def compile_run(tmp_path, source):
    compiler=shutil.which('g++')
    if not compiler:
        pytest.skip('C++ adapter compiler unavailable')
    file=tmp_path/'limits.cpp';file.write_text(source);out=tmp_path/'limits'
    subprocess.run([compiler,'-std=c++17',str(file),'-o',str(out)],check=True,capture_output=True,text=True)
    subprocess.run([str(out)],check=True,capture_output=True,text=True)


def test_account_limits_include_floating_costs_cash_and_persistence(tmp_path):
    s=Path('mt5/Ramon.mq5').read_text()
    fn=s[s.index('bool AccountLossLimitsBlocked('):s.index('bool ManageMainMaximumHold(')]
    adapter=r'''
#include <string>
#include <map>
#include <vector>
#include <cmath>
#include <cassert>
using string=std::string;using datetime=long;
bool EnableAccountLossLimits=true,history=true;
double DailyLossLimitPercent=2.,MaximumEquityDrawdownPercent=5.,balance=100.,equity=100.,credit=0.;
string AccountLossLimitStatus;long now=86400+1000;
enum{ACCOUNT_BALANCE,ACCOUNT_EQUITY,ACCOUNT_CREDIT,ACCOUNT_LOGIN,ACCOUNT_SERVER,DEAL_TYPE,DEAL_TIME,
 DEAL_PROFIT,DEAL_COMMISSION,DEAL_SWAP,DEAL_FEE,DEAL_TYPE_BALANCE,DEAL_TYPE_CREDIT,TRADE};
struct Deal{long type,time;double profit,commission=0,swap=0,fee=0;};
std::vector<Deal>deals;std::map<string,double>globals;
double AccountInfoDouble(int k){return k==ACCOUNT_BALANCE?balance:k==ACCOUNT_EQUITY?equity:credit;}
long AccountInfoInteger(int){return 123;}
string AccountInfoString(int){return "server";}
datetime TimeCurrent(){return now;}
bool HistorySelect(long,long){return history;}
int HistoryDealsTotal(){return deals.size();}
ulong HistoryDealGetTicket(int i){return i+1;}
long HistoryDealGetInteger(ulong d,int k){return k==DEAL_TYPE?deals[d-1].type:deals[d-1].time;}
double HistoryDealGetDouble(ulong d,int k){auto x=deals[d-1];return k==DEAL_PROFIT?x.profit:k==DEAL_COMMISSION?x.commission:k==DEAL_SWAP?x.swap:x.fee;}
string IntegerToString(long n){return std::to_string(n);}
string StringSubstr(string s,int off,int len){return s.substr(off,len);}
bool GlobalVariableCheck(string k){return globals.count(k);}
double GlobalVariableGet(string k){return globals[k];}
datetime GlobalVariableSet(string k,double v){globals[k]=v;return now;}
bool GlobalVariableSetOnCondition(string k,double v,double old){if(globals[k]!=old)return false;globals[k]=v;return true;}
void GlobalVariablesFlush(){}
double MathMax(double a,double b){return std::fmax(a,b);}
bool MathIsValidNumber(double x){return std::isfinite(x);}
'''
    cases=r'''
void reset(){EnableAccountLossLimits=true;history=true;DailyLossLimitPercent=2.;MaximumEquityDrawdownPercent=5.;
 balance=equity=100.;credit=0.;now=86400+1000;globals.clear();deals={{DEAL_TYPE_BALANCE,1000,100.}};}
int main(){string reason;
 reset();assert(!AccountLossLimitsBlocked(reason));equity=98.;assert(AccountLossLimitsBlocked(reason));assert(reason.find("daily")!=string::npos);
 equity=100.;assert(AccountLossLimitsBlocked(reason)); // daily latch survives recovery/restart
 now+=86400;assert(!AccountLossLimitsBlocked(reason)); // next broker day clears daily latch
 reset();assert(!AccountLossLimitsBlocked(reason));balance=99.;equity=99.;deals.push_back({TRADE,now,-.5,-.3,-.1,-.1});
 assert(!AccountLossLimitsBlocked(reason));equity=98.;assert(AccountLossLimitsBlocked(reason)); // realized fees + floating
 reset();DailyLossLimitPercent=0;assert(!AccountLossLimitsBlocked(reason));equity=95.;assert(AccountLossLimitsBlocked(reason));
 assert(reason.find("drawdown")!=string::npos);equity=96.;assert(!AccountLossLimitsBlocked(reason));
 reset();assert(!AccountLossLimitsBlocked(reason));deals.push_back({DEAL_TYPE_BALANCE,now,-20});balance=equity=80.;assert(!AccountLossLimitsBlocked(reason));
 reset();assert(!AccountLossLimitsBlocked(reason));deals.push_back({DEAL_TYPE_BALANCE,now,20});balance=equity=120.;assert(!AccountLossLimitsBlocked(reason));
 equity=117.6;assert(AccountLossLimitsBlocked(reason)); // today's opening balance stays 100 despite deposit
 reset();assert(!AccountLossLimitsBlocked(reason));deals.push_back({DEAL_TYPE_CREDIT,now,20});credit=20.;equity=120.;assert(!AccountLossLimitsBlocked(reason));
 equity=118.;assert(AccountLossLimitsBlocked(reason)); // credit is not fictional daily profit
 reset();history=false;assert(AccountLossLimitsBlocked(reason));
 reset();EnableAccountLossLimits=false;history=false;assert(!AccountLossLimitsBlocked(reason));
 reset();DailyLossLimitPercent=MaximumEquityDrawdownPercent=0;assert(AccountLossLimitsBlocked(reason));
 reset();DailyLossLimitPercent=100;assert(AccountLossLimitsBlocked(reason));
 reset();equity=std::nan("");assert(AccountLossLimitsBlocked(reason));
 reset();DailyLossLimitPercent=std::nan("");assert(AccountLossLimitsBlocked(reason));
}
'''
    compile_run(tmp_path,adapter+fn+cases)


def test_actual_quick_target_reward_risk_both_directions(tmp_path):
    s=Path('mt5/Ramon.mq5').read_text()
    fn=s[s.index('bool RangeMainRewardRiskValid('):s.index('bool RangeMainProfitTarget(')]
    adapter=r'''
#include <string>
#include <cassert>
using string=std::string;enum ENUM_ORDER_TYPE{ORDER_TYPE_BUY,ORDER_TYPE_SELL};string _Symbol="XAUUSD";
bool available=true;
bool OrderCalcProfit(ENUM_ORDER_TYPE side,string,double v,double entry,double price,double &out){out=(side==ORDER_TYPE_BUY?price-entry:entry-price)*v;return available;}
'''
    cases=r'''
int main(){assert(!RangeMainRewardRiskValid(ORDER_TYPE_BUY,100,1,95,105));
 assert(RangeMainRewardRiskValid(ORDER_TYPE_BUY,100,1,96,105));
 assert(!RangeMainRewardRiskValid(ORDER_TYPE_SELL,100,1,105,95));
 assert(RangeMainRewardRiskValid(ORDER_TYPE_SELL,100,1,104,95));
 available=false;assert(!RangeMainRewardRiskValid(ORDER_TYPE_BUY,100,1,96,105));}
'''
    compile_run(tmp_path,adapter+fn+cases)
