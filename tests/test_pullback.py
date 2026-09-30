"""Execute actual MQL pattern logic; alternative outcomes must not train Chronos."""
from pathlib import Path
import shutil
import sqlite3
import subprocess
import pytest
from ramon.history import persist_trade_outcome

EA=Path(__file__).parents[1]/'mt5/Ramon.mq5'

def test_actual_closed_candle_pattern(tmp_path):
    compiler=shutil.which('g++')
    if not compiler:
        pytest.skip('g++ needed for MQL adapter')
    source=EA.read_text()
    fn=source[source.index('bool PullbackPattern('):source.index('string AdvanceLiveEvidence(')]
    fn=fn.replace('const MqlRates &hourly[]','const std::vector<MqlRates> &hourly').replace('const MqlRates &minute[]','const std::vector<MqlRates> &minute')
    harness=r'''
#include <string>
#include <vector>
#include <cassert>
#include <algorithm>
using string=std::string;using datetime=long;
struct MqlRates {datetime time;double open,high,low,close;};
#define ArraySize(x) ((int)x.size())
#define MathMin std::min
#define MathMax std::max
'''
    cases=r'''
int main(){datetime now=200000; string direction; double stop;
 std::vector<MqlRates> h(32),m(4);
 for(int i=0;i<32;i++) h[i]={now-3600*(i+1),100,200,1,150.-i};
 m[3]={now-240,100,104,99,103};m[2]={now-180,103,104,100,102};
 m[1]={now-120,102,103,99,101};m[0]={now-60,101,106,100,105};
 assert(PullbackPattern(h,m,now,direction,stop)&&direction=="BUY"&&stop==99);
 auto original=m;
 m[0].time=now;assert(!PullbackPattern(h,m,now,direction,stop));
 m=original;m[2].time-=60;assert(!PullbackPattern(h,m,now,direction,stop));
 m=original;assert(!PullbackPattern(h,m,now+121,direction,stop));
 m[0].close=102;assert(!PullbackPattern(h,m,now,direction,stop));
 m=original;m[1].close=103;assert(!PullbackPattern(h,m,now,direction,stop));
 m=original;
 for(auto &bar:h)bar.close=300-bar.close;
 for(auto &bar:m){double high=bar.high;bar.open=300-bar.open;bar.close=300-bar.close;
 bar.high=300-bar.low;bar.low=300-high;}
 assert(PullbackPattern(h,m,now,direction,stop)&&direction=="SELL"&&stop==201);
 h[5].time-=3600;assert(!PullbackPattern(h,m,now,direction,stop));
}
'''
    cpp=tmp_path/'pattern.cpp';cpp.write_text(harness+fn+cases)
    binary=tmp_path/'pattern'
    subprocess.run([compiler,'-std=c++17',str(cpp),'-o',str(binary)],check=True,capture_output=True)
    subprocess.run([str(binary)],check=True,capture_output=True)


def test_alternative_outcome_stays_excluded_after_legacy_retry(tmp_path):
    db=tmp_path/'history.sqlite3'
    payload=dict(trade_key='s:1:2',sample_key='a'*16,symbol='XAUUSD_l',direction='BUY',
        opened=1800010000,closed=1800010900,net_units=3.,initial_risk_units=2.,
        exit_reason='DEAL_REASON_TP',entry_strategy='h1_m1_pullback')
    persist_trade_outcome(db,payload,1800011000)
    del payload['entry_strategy']
    persist_trade_outcome(db,payload,1800011001)
    with sqlite3.connect(db) as conn:
        assert conn.execute('SELECT entry_strategy,training_status FROM trade_outcomes').fetchone()==(
            'h1_m1_pullback','CENSORED_ALTERNATIVE_STRATEGY')


def test_replacement_order_path_keeps_structural_and_shared_caps():
    source=EA.read_text();timer=source.split('void OnTimer()',1)[1]
    assert 'SmallProfitCandidate(' not in timer
    assert 'SmallProfitStop(' not in timer
    assert 'stop_loss_units>SmallProfitRiskCapUnits()+0.00001' in timer
    assert 'stressed_entry' in timer
    assert 'MathAbs(spread_cost)>-stop_loss_units*0.25' in timer
    assert 'small_entries_on_bar>=1' in timer
    assert timer.index('SharedRiskAllowsEntry(')<timer.index('Trade.Buy(')
    assert 'version=="0.62"' in source.split('void RecordDealTelemetry',1)[-1]
