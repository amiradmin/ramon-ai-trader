from pathlib import Path


def test_demo_experimental_version_and_account_restriction():
    source=Path("mt5/Ramon.mq5").read_text()
    assert '#property version "1.610"' in source
    assert '#define RAMON_EA_VERSION "0.61.0"' in source
    assert 'input bool DemoExperimentalMode = false;' in source
    assert 'DemoExperimentalMode && AccountInfoInteger(ACCOUNT_TRADE_MODE)!=ACCOUNT_TRADE_MODE_DEMO' in source
    assert 'DemoExperimentalMode && AccountInfoInteger(ACCOUNT_TRADE_MODE)==ACCOUNT_TRADE_MODE_DEMO' in source


def test_only_frequency_gates_bypassed_and_risk_guards_kept():
    source=Path("mt5/Ramon.mq5").read_text()
    assert 'if(DemoExperimentActive()) { reason=""; return false; }' in source
    assert 'if(DemoExperimentActive()) return 0;' in source
    assert 'if(!DemoExperimentActive() && (today<0 || today>=MaxTradesPerDay))' in source
    assert 'if(!V2Preflight(side,entry,stop,volume,guard_reason))' in source
    assert 'if(!LiveExecutionReady(guard_reason) || AccountLossLimitsBlocked(guard_reason)' in source
    assert 'input bool EnableLiveTrading = false;' in source


def test_collector_version():
    assert '#property version "1.01"' in Path("mt5/RamonM1M5Collector.mq5").read_text()
