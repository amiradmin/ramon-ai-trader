"""Source extraction/isolation checks; not fixture runtime or broker acceptance."""
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from generate_stopout_history_harness import create_harness


def test_harness_keeps_production_functions_and_mocks_stateful_dependencies():
    source=(ROOT/'mt5/Ramon.mq5').read_text(encoding='utf-8-sig')
    harness=create_harness(source)
    guard=source[source.index('string V2StopoutKey()'):source.index('\nvoid OnTradeTransaction(')]
    assert guard in harness
    for name in ['AccountInfoInteger','AccountInfoString','TerminalInfoInteger',
                 'TimeCurrent','HistorySelect','HistoryDealsTotal',
                 'HistoryDealGetTicket','HistoryDealGetInteger',
                 'GlobalVariableCheck','GlobalVariableGet','GlobalVariableSet',
                 'GlobalVariablesFlush']:
        assert f'#define {name} Mock{name}' in harness
        assert harness.index(f'#define {name} Mock{name}')<harness.index(guard)
    for forbidden in ['OrderSend(', 'OrderSendAsync(', 'CTrade', 'Trade.',
                      'GlobalVariableDel(', '#import', '#include']:
        assert forbidden not in harness


def test_recovery_does_not_create_review_markers_or_delete_existing_lock():
    source=(ROOT/'mt5/Ramon.mq5').read_text(encoding='utf-8-sig')
    helpers=source[source.index('string V2StopoutKey()'):source.index('bool V2Preflight(')]
    assert 'GlobalVariableSet(V2StopoutReviewKey' not in helpers
    assert 'GlobalVariableDel' not in helpers
    assert 'if(GlobalVariableCheck(V2StopoutKey())) return true;' in helpers
    assert 'value==(double)deal_msc' in helpers
    assert 'StringFormat("%I64u",ticket)' in helpers
