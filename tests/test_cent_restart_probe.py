"""Source guardrails for the explicitly authorized order-free Cent probe."""
import sys
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))
from generate_cent_restart_probe import create_cent_probe

SOURCE = (ROOT/'mt5/Ramon.mq5').read_text(encoding='utf-8-sig')


@pytest.mark.parametrize('seed', [True, False])
def test_live_probe_is_bound_flat_disarmed_and_order_free(seed):
    code = create_cent_probe(SOURCE, 1234, 'Example-Live', 9999, seed)
    assert 'const long expected_login=1234;' in code
    assert 'const string expected_server="Example-Live";' in code
    for token in ['ACCOUNT_TRADE_MODE_REAL', 'TERMINAL_TRADE_ALLOWED',
                  'PositionsTotal()!=0 || OrdersTotal()!=0', 'reason!="stop-out lock"']:
        assert token in code
    assert code.index('TERMINAL_TRADE_ALLOWED') < code.index('GlobalVariableSet(key,')
    assert code.index('PositionsTotal()!=0') < code.index('GlobalVariableSet(key,')
    for forbidden in ['OrderSend(', 'OrderSendAsync(', 'CTrade', 'Trade.',
                      'GlobalVariableDel(', '#import', '#include']:
        assert forbidden not in code
    assert 'TerminalClose(0);\n   return;' in code


def test_binding_rejects_injected_or_missing_identity():
    with pytest.raises(ValueError):
        create_cent_probe(SOURCE, 1, 'bad"server', 1, True)
    with pytest.raises(ValueError):
        create_cent_probe(SOURCE, 0, 'Live', 1, True)
