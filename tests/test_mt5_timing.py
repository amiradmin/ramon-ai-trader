"""Execute the EA's arithmetic timing helpers without terminal or trade access."""
from pathlib import Path
import re
import shutil
import subprocess

import pytest

EA = Path(__file__).parents[1] / 'mt5' / 'Ramon.mq5'


@pytest.fixture(scope='module')
def timing_binary(tmp_path_factory):
    compiler = shutil.which('g++')
    if not compiler:
        pytest.skip('C++ compiler unavailable for MQL-compatible arithmetic')
    source = EA.read_text()
    helpers = source[source.index('void UpdateWeakConfirmation('):source.index('void ReadControlRiskCap()')]
    spacing = re.search(r'const int ExitWeakSnapshotSpacingSeconds = \d+;', source).group()
    root = tmp_path_factory.mktemp('ea-timing')
    cpp = root / 'timing.cpp'
    cpp.write_text('''#include <cassert>
#include <string>
using datetime = long long;
using ulong = unsigned long;
''' + spacing + '\n' + helpers + '''
int main(int argc, char **argv) {
  assert(argc==2);
  if(std::string(argv[1])=="weak") {
    int count=0; datetime last=0;
    UpdateWeakConfirmation(true,100,count,last);
    assert(count==1 && last==100);
    for(int now=105;now<130;now+=5) {
      UpdateWeakConfirmation(true,now,count,last);
      assert(count==1 && last==100);
    }
    UpdateWeakConfirmation(true,130,count,last);
    assert(count==2 && last==130);
    UpdateWeakConfirmation(true,130,count,last); // duplicate evidence
    assert(count==2);
    UpdateWeakConfirmation(false,131,count,last); // support resets confirmation
    assert(count==0 && last==0);
    UpdateWeakConfirmation(true,132,count,last);
    UpdateWeakConfirmation(true,130,count,last); // out-of-order clock
    assert(count==1 && last==132);
    UpdateWeakConfirmation(true,161,count,last);
    assert(count==1);
    UpdateWeakConfirmation(true,162,count,last);
    assert(count==2);
    UpdateWeakConfirmation(false,163,count,last);
    UpdateWeakConfirmation(true,200,count,last);
    UpdateWeakConfirmation(true,260,count,last); // slower legacy cadence also works
    assert(count==2);

  }
}
''')
    binary = root / 'timing'
    subprocess.run([compiler, '-std=c++17', str(cpp), '-o', str(binary)], check=True, capture_output=True)
    return binary


def test_five_second_samples_cannot_accelerate_weak_exit(timing_binary):
    subprocess.run([str(timing_binary), 'weak'], check=True)


def test_all_weak_exit_paths_use_independent_confirmation_clock():
    source = EA.read_text()
    for prefix in ('EarlyAdverse', 'MainFastProfit', 'TPStage'):
        assert f'{prefix}WeakSnapshots,{prefix}LastWeakCountTime);' in source
        assert f'{prefix}WeakSnapshots++;' not in source
    assert source.count('now-LastModelSnapshotTime>ExitModelFreshnessSeconds') == 2
    assert 'now-LastModelSnapshotTime<=ExitModelFreshnessSeconds' in source
    timer = source.split('void OnTimer()', 1)[1].split('void OnTradeTransaction(', 1)[0]
    assert 'now-LastPositionManagementTime>=5' in timer
    assert timer.index('ManageOpenPosition();') < timer.index('SyncClosedTrades();')
    assert 'EventSetTimer(1)' in source
