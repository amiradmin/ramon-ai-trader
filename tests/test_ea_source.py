"""Static regression checks for the MT5 Expert source."""

from pathlib import Path


EA = Path(__file__).resolve().parents[1] / "mt5" / "Ramon.mq5"


def test_model_url_allows_host_and_compose_service_only() -> None:
    source = EA.read_text(encoding="utf-8")

    assert '#property version "0.17"' in source
    assert 'url=="http://127.0.0.1:8012/decision"' in source
    assert 'url=="http://model:8012/decision"' in source
    assert "!IsAllowedModelUrl(ModelUrl)" in source
    assert 'StringFind(ModelUrl,"http://127.0.0.1:")!=0' not in source


def test_live_account_session_lock() -> None:
    source = EA.read_text(encoding="utf-8")

    assert '#property version "0.17"' in source
    assert "input bool AutoLockCurrentAccount = true" in source
    assert "input bool EnableLiveTrading = false" in source
    assert "LockedAccountLogin=current_login" in source
    assert "bool AccountLockHealthy()" in source
    assert 'StatusLine="Account/server lock mismatch"' in source
    assert "RiskPerTradeUSD = 0.06" in source


def test_diagnostic_export_contains_operational_state() -> None:
    source = EA.read_text(encoding="utf-8")

    assert 'input string DiagnosticFileName = "Ramon_Diagnostic.txt"' in source
    assert "FILE_COMMON" in source
    assert "string BuildDiagnosticText()" in source
    assert '"=== RAMON DIAGNOSTIC ===\\n"' in source
    assert '"=== MODEL / SIGNAL ===\\n"' in source
    assert '"=== ACCOUNT / EXECUTION ===\\n"' in source
    assert 'JsonNumber(reply,"forecast_low",forecast_low)' in source
    assert 'JsonNumber(reply,"forecast_high",forecast_high)' in source
    assert 'JsonNumber(reply,"edge",edge)' in source
    assert 'JsonNumber(reply,"spread_points",model_spread)' in source


def test_chart_dashboard_and_copy_button() -> None:
    source = EA.read_text(encoding="utf-8")

    assert 'RAMON AI TRADER  v0.17' in source
    assert 'UiButton("COPY","COPY DIAGNOSTIC"' in source
    assert "void OnChartEvent(" in source
    assert "CopyDiagnosticToClipboard()" in source
    assert "MQL_DLLS_ALLOWED" in source
    assert "SetClipboardData" in source
    assert 'JsonNumber(reply,"buy_edge",buy_edge)' in source
    assert 'JsonNumber(reply,"sell_edge",sell_edge)' in source
    assert 'JsonNumber(reply,"signal_strength",signal_strength)' in source
    assert '"EDGE "+PassFail(edge_pass)' in source
    assert '"STRENGTH "+PassFail(strength_pass)' in source


def test_risk_verification_and_csv_learning_logs() -> None:
    source = EA.read_text(encoding="utf-8")

    assert "input bool ConfirmMoneyUnitsPerUSD = false" in source
    assert 'input string ExpectedAccountCurrency = ""' in source
    assert 'input string SignalCsvFileName = "Ramon_Signals.csv"' in source
    assert 'input string TradeCsvFileName = "Ramon_Trades.csv"' in source
    assert "void UpdateSizingPreview()" in source
    assert "RiskBudgetAccountUnits:" in source
    assert "EstimatedSLAccountUnits:" in source
    assert 'JsonNumber(reply,"signal_bid",signal_bid)' in source
    assert 'JsonNumber(reply,"signal_ask",signal_ask)' in source
    assert "void AppendSignalCsv()" in source
    assert "void AppendTradeCsv(const ulong deal)" in source
    assert "void OnTradeTransaction(" in source
    assert 'reason="BLOCKED: confirm MoneyUnitsPerUSD"' in source
    assert 'reason="BLOCKED: account currency mismatch"' in source
    assert "string LiveStateText()" in source
    assert 'Print("Ramon live BLOCKED: ConfirmMoneyUnitsPerUSD is false")' in source
    assert 'ConfirmMoneyUnitsPerUSD must be true before live trading' not in source


def test_live_block_does_not_detach_ea() -> None:
    source = EA.read_text(encoding="utf-8")

    assert '#property version "0.17"' in source
    assert "bool LiveExecutionReady(string &reason)" in source
    assert 'return (LiveExecutionReady(reason) ? "ARMED" : "BLOCKED")' in source
    assert 'if(!LiveExecutionReady(live_block_reason))' in source
    assert 'StatusLine=live_block_reason' in source
    assert 'Print("Ramon live BLOCKED: ConfirmMoneyUnitsPerUSD is false")' in source
    assert 'Print("Ramon live BLOCKED: account/server lock mismatch")' in source


def test_cent_account_dashboard_converts_units_to_usd() -> None:
    source = EA.read_text(encoding="utf-8")

    assert '#property version "0.17"' in source
    assert "input bool AccountIsCent = true" in source
    assert "string AccountTypeText()" in source
    assert "double AccountUnitsToUSD(const double units)" in source
    assert "bool MinimumLotExceedsRiskBudget()" in source
    assert '"AccountType: "+AccountTypeText()+" (configured)"' in source
    assert '"BalanceUSDApprox: "+DoubleToString(AccountUnitsToUSD(AccountInfoDouble(ACCOUNT_BALANCE)),2)' in source
    assert '"MinExecutableRiskUSD: "+DoubleToString(AccountUnitsToUSD(LastMinimumLotStopLossUnits),4)' in source
    assert '"TRADE BLOCKED: min lot > risk budget"' in source
    assert '"Min executable risk: $"' in source
    assert '"balance_usd_approx"' in source
    assert '"min_executable_risk_usd"' in source
