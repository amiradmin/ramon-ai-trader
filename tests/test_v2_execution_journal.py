"""Production coordinator translated to C++, Linux IO, synthetic broker responses.

No MT5/Wine mutual-exclusion or broker-side acceptance is claimed.
"""
from pathlib import Path
import shutil
import subprocess
import sys
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))
from generate_execution_journal_adapter import create_adapter


@pytest.fixture(scope='module')
def coordinator(tmp_path_factory):
    if not shutil.which('g++') or not Path('/usr/include/openssl/sha.h').exists():
        pytest.skip('g++ and OpenSSL development headers required')
    folder = tmp_path_factory.mktemp('execution-adapter')
    cpp = folder/'coordinator.cpp'
    cpp.write_text(create_adapter((ROOT/'mt5/Ramon.mq5').read_text(encoding='utf-8-sig')))
    binary = folder/'coordinator'
    result = subprocess.run(['g++', '-std=c++17', str(cpp), '-lcrypto', '-o', str(binary)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return binary


@pytest.fixture
def run(coordinator, tmp_path):
    def invoke(op, id='intent-a', mode='full', scope='fixture-a'):
        return subprocess.run([str(coordinator),op,id,mode,scope], cwd=tmp_path, capture_output=True, text=True, timeout=10)
    assert invoke('provision').returncode == 0
    return invoke


def test_unprovisioned_and_wrong_scope_are_blocked(run):
    assert run('check', scope='other-account').returncode == 10
    assert run('provision').returncode != 0  # Never overwrite existing state.


@pytest.mark.parametrize('route', ['prepare', 'guardian', 'crash'])
def test_unresolved_preparation_survives_process_exit_without_retry(run, route):
    # Guardian success is separately tested; prepare it with an ambiguous response.
    mode = 'timeout' if route=='guardian' else 'full'
    assert run(route, mode=mode).returncode == 0
    assert run('check').returncode == 10
    assert run('check', id='intent-b').returncode == 10


@pytest.mark.parametrize('mode', ['timeout', 'placed', 'partial', 'disconnect', 'changed', 'missing', 'history_error', 'wrong_order', 'netting', 'no_sl', 'unknown_ids'])
def test_uncertain_result_never_releases_reservation_without_evidence(run, mode):
    assert run('submit', mode=mode).returncode == 0
    # Preserve the uncertainty in the synthetic broker state after restart.
    result = run('check', id='intent-b', mode=mode)
    assert result.returncode == 10, result.stdout


@pytest.mark.parametrize('route,mode', [('submit','full'),('guardian','full'),('submit','reject'),('cancel','full')])
def test_confirmed_transfer_rejection_or_presend_cancellation_allows_new_intent(run, route, mode):
    assert run(route, mode=mode).returncode == 0
    assert run('check', id='intent-b').returncode == 0
    assert run('check').returncode == 10  # Same intent remains permanently consumed.


def test_partial_then_complete_visible_fill_can_be_reconciled_after_restart(run):
    assert run('submit', mode='partial').returncode == 0
    assert run('check', id='intent-b', mode='partial').returncode == 10
    assert run('check', id='intent-b', mode='full').returncode == 0
    assert run('check').returncode == 10


@pytest.mark.parametrize('mode', ['write_error', 'flush_error'])
def test_no_action_follows_storage_failure(run, mode):
    result = run('prepare', mode=mode)
    assert result.returncode == 11
    assert 'PREPARED\n' not in result.stdout
    if mode=='flush_error':
        assert run('check', id='intent-b').returncode == 10


def test_torn_journal_blocks_new_entries(run, tmp_path):
    journal = next(tmp_path.glob('*.journal'))
    with journal.open('ab') as f:
        f.write(b'E1|torn')
    assert run('check').returncode == 10


def test_os_lease_serializes_different_routes_across_processes(coordinator, run, tmp_path):
    first = subprocess.Popen([str(coordinator),'hold','ordinary','full'],cwd=tmp_path,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    try:
        assert first.stdout.readline().strip() == 'PREPARED'
        # Chart B cannot check/close a Guardian parent while chart A owns the file.
        second = run('guardian', id='guardian-other')
        assert second.returncode == 10
        assert 'busy' in second.stdout
        assert first.wait(timeout=5) == 0
        assert run('check', id='dashboard-small').returncode == 10
    finally:
        if first.poll() is None:
            first.kill()
            first.wait()


def test_all_broker_entry_sites_are_enclosed_by_lease_and_durable_preparation():
    source = (ROOT/'mt5/Ramon.mq5').read_text(encoding='utf-8-sig')
    normal = source[source.index('   string execution_id=LastSampleKey'):source.index('// V2 account-level entry guard:')]
    guardian = source[source.index('bool ProcessGuardianReversals()'):source.index('void ProcessGuardianRecoveryExits()')]
    assert normal.index('V2ExecBegin(')<normal.index('V2Preflight(')<normal.index('V2ExecReserve(')<normal.index('Trade.Buy(')<normal.index('V2ExecRecordResult(')
    assert guardian.index('V2ExecutionLease execution_lease')<guardian.index('V2ExecBegin(')<guardian.index('V2ExecReserve(')<guardian.index('Trade.PositionClose(')<guardian.index('V2Preflight(')<guardian.index('V2ExecAmend(')<guardian.index('Trade.Buy(')<guardian.index('V2ExecRecordResult(')
    assert source.count('Trade.Buy(')==2 and source.count('Trade.Sell(')==2
    assert 'Trade.SetAsyncMode(false)' in source
    core = source.split('// BEGIN V2 EXECUTION JOURNAL CORE')[1].split('// END V2 EXECUTION JOURNAL CORE')[0]
    assert 'FILE_SHARE_READ' not in core.replace('// No FILE_SHARE_READ/WRITE:', '')
    assert 'FileDelete(' not in core and 'FileMove(' not in core


def test_placed_order_resolves_only_after_full_broker_visibility(run):
    assert run('submit', mode='placed').returncode == 0
    assert run('check', id='intent-b', mode='placed').returncode == 10
    assert run('check', id='intent-b', mode='full').returncode == 0
    assert run('check').returncode == 10


def test_provisioner_is_explicit_first_use_and_has_no_broker_write_api():
    from generate_execution_journal_provisioner import create_provisioner
    source = create_provisioner((ROOT/'mt5/Ramon.mq5').read_text(encoding='utf-8-sig'))
    assert 'input bool ConfirmFirstUseOnly=false' in source
    assert 'ExpectedLogin>0' in source and 'ExpectedServer!=""' in source
    assert 'FileIsExist(path,FILE_COMMON)' in source and 'FileSize(V2ExecHandle)!=0' in source
    assert 'PositionsTotal()!=0 || OrdersTotal()!=0' in source
    for forbidden in ['OrderSend(', 'CTrade', 'Trade.', 'FileDelete(', 'GlobalVariableDel(']:
        assert forbidden not in source


def test_sell_receipt_transfers_to_matching_sell_position(run):
    assert run('submit', mode='sell').returncode == 0
    assert run('check', id='intent-b', mode='sell').returncode == 0
    assert run('check', mode='sell').returncode == 10


def test_crash_after_receipt_reconciles_without_resending_original(run):
    assert run('crash_receipt').returncode == 0
    assert run('check', id='intent-b', mode='missing').returncode == 10
    assert run('check', id='intent-b', mode='full').returncode == 0
    assert run('check').returncode == 10
