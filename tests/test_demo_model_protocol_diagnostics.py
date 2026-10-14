"""Static contract checks for explicit model protocol rejection diagnostics.

These checks do not compile MQL5 or exercise a real MetaTrader terminal.
"""
from pathlib import Path
import re

SOURCE = Path("mt5/Ramon.mq5")


def test_rejected_reply_names_first_missing_field():
    s = SOURCE.read_text()
    block = s[s.index('if(!JsonText(reply,"decision",decision)'):
              s.index('// Optional protocol: old servers omit this field')]
    required = set(re.findall(r'!Json(?:Text|Number)\(reply,"([^"]+)"', block.split('{ LastModelSnapshotTime=0')[0]))
    diagnosed = set(re.findall(r'snapshot_invalid_field="([^"]+)"', block))
    assert required <= diagnosed
    assert len(required) >= 70
    assert 'RAMON_MODEL_PROTOCOL_REJECT symbol=' in block
    assert 'signal_bar_time_mismatch' in block
    assert 'StatusLine="Invalid model response: "+snapshot_invalid_field' in block


def test_demo_usd_guard_precedes_currency_confirmation():
    s = SOURCE.read_text()
    block = s[s.index('bool LiveExecutionReady('):s.index('string LiveStateText()')]
    assert 'DemoExperimentActive() && (AccountIsCent' in block
    assert 'MathAbs(MoneyUnitsPerUSD-1.0)>0.000001' in block
    assert 'AccountInfoString(ACCOUNT_CURRENCY)!="USD"' in block
    assert block.index('DemoExperimentActive()') < block.index('if(!ConfirmMoneyUnitsPerUSD)')


def test_demo_mode_restricted_to_demo_account():
    s = SOURCE.read_text()
    assert 'DemoExperimentalMode && AccountInfoInteger(ACCOUNT_TRADE_MODE)!=ACCOUNT_TRADE_MODE_DEMO' in s
