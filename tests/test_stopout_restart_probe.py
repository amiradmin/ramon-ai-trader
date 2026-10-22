"""Offline source guardrails, not MQL compilation or terminal restart tests."""
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('probe', ROOT / 'scripts/generate_stopout_restart_probe.py')
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)
SOURCE = (ROOT / 'mt5/Ramon.mq5').read_text(encoding='utf-8-sig')


def test_exact_production_guard_and_order_free_context():
    result = probe.create_probe(SOURCE)
    guard = SOURCE[SOURCE.index('string V2StopoutKey()'):SOURCE.index('\nvoid OnTradeTransaction(')]
    import hashlib
    assert hashlib.sha256(guard.encode()).hexdigest() in result
    assert 'string V2StopoutKey()' in result
    assert 'bool V2ProbeReadOnlyPreflight(string &reason)' in result
    assert 'V2ReleaseKnownTestLock' not in result
    assert 'V2ReleaseReviewedStopoutLock' not in result
    assert 'V2Preflight(' not in result
    for token in ('ACCOUNT_TRADE_MODE_DEMO', 'ExpectedDemoLogin<=0',
                  'AccountInfoString(ACCOUNT_SERVER)!=ExpectedDemoServer',
                  'GlobalVariablesFlush()', 'reason!="stop-out lock"',
                  'GlobalVariableGet(key)!=(double)ExpectedLockValue'):
        assert token in result
    assert result.index('ACCOUNT_TRADE_MODE_DEMO') < result.index('GlobalVariableSet(key,')
    assert 'GlobalVariableDel' not in result
    assert 'PASS' not in result


@pytest.mark.parametrize('mutation', [
    lambda s: s.replace(
        'string V2StopoutKey()',
        'string V2StopoutKeyMissing()',
        1,
    ),
    lambda s: s.replace(
        'string V2StopoutKey() { return',
        'string V2StopoutKey() { OrderSendAsync(); return',
        1,
    ),
])
def test_unrecognized_or_execution_dependency_fails_closed(mutation):
    with pytest.raises(ValueError):
        probe.create_probe(mutation(SOURCE))
