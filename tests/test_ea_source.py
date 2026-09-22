"""Static regression checks for the MT5 Expert source."""

from pathlib import Path


EA = Path(__file__).resolve().parents[1] / "mt5" / "Ramon.mq5"


def test_model_url_allows_host_and_compose_service_only() -> None:
    source = EA.read_text(encoding="utf-8")

    assert '#property version "0.22"' in source
    assert 'url=="http://127.0.0.1:8012/decision"' in source
    assert 'url=="http://model:8012/decision"' in source
    assert "!IsAllowedModelUrl(ModelUrl)" in source
    assert 'StringFind(ModelUrl,"http://127.0.0.1:")!=0' not in source


def test_live_account_session_lock() -> None:
    source = EA.read_text(encoding="utf-8")

    assert '#property version "0.22"' in source
    assert "input bool AutoLockCurrentAccount = true" in source
    assert "input bool EnableLiveTrading = false" in source
    assert "LockedAccountLogin=current_login" in source
    assert "bool AccountLockHealthy()" in source
    assert 'reason="BLOCKED: account/server lock mismatch"' in source
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

    assert 'RAMON AI TRADER  v0.22' in source
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

    assert '#property version "0.22"' in source
    assert "bool LiveExecutionReady(string &reason)" in source
    assert 'return (LiveExecutionReady(reason) ? "ARMED" : "BLOCKED")' in source
    assert 'if(!LiveExecutionReady(live_block_reason))' in source
    assert 'StatusLine=live_block_reason' in source
    assert 'Print("Ramon live BLOCKED: ConfirmMoneyUnitsPerUSD is false")' in source
    assert 'Print("Ramon live BLOCKED: account/server lock mismatch")' in source


def test_cent_account_dashboard_converts_units_to_usd() -> None:
    source = EA.read_text(encoding="utf-8")

    assert '#property version "0.22"' in source
    assert "input bool AccountIsCent = true" in source
    assert "string AccountTypeText()" in source
    assert "double AccountUnitsToUSD(const double units)" in source
    assert "bool MinimumLotExceedsRiskBudget()" in source
    assert '"AccountType: "+AccountTypeText()+" (configured)"' in source
    assert '"  BalanceUSDApprox: "+DoubleToString(AccountUnitsToUSD(AccountInfoDouble(ACCOUNT_BALANCE)),2)' in source
    assert '"  MinExecutableRiskUSD: "+DoubleToString(AccountUnitsToUSD(LastMinimumLotStopLossUnits),4)' in source
    assert '"TRADE BLOCKED: min lot > hard cap"' in source
    assert '"Min executable risk: $"' in source
    assert '"balance_usd_approx"' in source
    assert '"min_executable_risk_usd"' in source


def test_wait_display_distinguishes_strength_and_future_risk_block() -> None:
    source = EA.read_text(encoding="utf-8")

    assert '#property version "0.22"' in source
    assert "string RiskGateText()" in source
    assert '"WOULD BLOCK IF SIGNAL: min lot > hard cap"' in source
    assert '+"RiskGate: "+RiskGateText()+"\\n"' in source
    assert 'UiLabel("RISK_GATE",RiskGateText()' in source


def test_live_snapshots_re_evaluate_inside_same_m15_bar() -> None:
    source = EA.read_text(encoding="utf-8")

    assert '#property version "0.22"' in source
    assert "input int SnapshotIntervalSeconds = 30" in source
    assert "datetime LastDecisionRequestTime = 0" in source
    assert "datetime LastEntrySignalBar = 0" in source
    assert "now-LastDecisionRequestTime<SnapshotIntervalSeconds" in source
    assert "closed==LastProcessedBar" not in source
    assert "LastProcessedBar=bar_time" not in source
    assert 'StatusLine="Entry already used for this M15 signal bar"' in source
    assert "LastEntrySignalBar=bar_time" in source
    assert "SnapshotIntervalSeconds<10" in source


def test_intrabar_reversal_payload_and_dashboard() -> None:
    source = EA.read_text(encoding="utf-8")

    assert '#property version "0.22"' in source
    assert "CopyRates(_Symbol,PERIOD_M1,0,4,micro)" in source
    assert '\\"micro_bars\\":[' in source
    assert 'JsonNumber(reply,"intrabar_confirmed",intrabar_confirmed)' in source
    assert 'JsonText(reply,"intrabar_direction",intrabar_direction)' in source
    assert 'UiLabel("MICRO","INTRABAR "+PassFail(LastIntrabarConfirmed)' in source
    assert "LastIntrabarConfirmed=(intrabar_confirmed>=0.5)" in source
    assert '"intrabar_reversal_up"' not in source  # decision reason belongs to Python core


def test_ai_trend_continuation_is_model_path_led() -> None:
    source = EA.read_text(encoding="utf-8")

    assert '#property version "0.22"' in source
    assert 'JsonNumber(reply,"ai_trend_confirmed",ai_trend_confirmed)' in source
    assert 'JsonText(reply,"ai_trend_direction",ai_trend_direction)' in source
    assert 'UiLabel("AI_TREND","AI TREND "+PassFail(LastAiTrendConfirmed)' in source
    assert "LastAiTrendConfirmed=(ai_trend_confirmed>=0.5)" in source
    assert "iMA(" not in source
    assert "iRSI(" not in source


def test_minimum_lot_override_has_hard_cap() -> None:
    source = EA.read_text(encoding="utf-8")

    assert '#property version "0.22"' in source
    assert "input double RiskPerTradeUSD = 0.06" in source
    assert "input bool AllowMinLotRiskOverride = true" in source
    assert "input double MaxExecutableRiskUSD = 0.20" in source
    assert "bool MinimumLotOverrideEligible()" in source
    assert "bool RiskGateBlocked()" in source
    assert "double MaxExecutableRiskUnits()" in source
    assert 'return "PASS: MIN LOT OVERRIDE <= $"+DoubleToString(MaxExecutableRiskUSD,2)' in source
    assert 'StatusLine="TRADE BLOCKED: min lot > hard risk cap"' in source
    assert "return minimum;" in source
    assert "MaxExecutableRiskUSD>0.50" in source
    assert "MaxExecutableRiskUSD<RiskPerTradeUSD" in source
    assert '"max_executable_risk_usd"' in source
    assert '"min_lot_override_used"' in source


def test_v022_dashboard_rows_are_not_overlapped() -> None:
    source = EA.read_text(encoding="utf-8")

    assert 'UiLabel("AI_TREND"' in source
    assert '),28,288,' in source
    assert 'UiLabel("RISK","ATR: "' in source
    assert '),28,310,' in source
    assert 'UiLabel("ACCOUNT","Account: "' in source
    assert '),28,332,' in source
    assert 'UiLabel("BALANCE_USD","Balance: "' in source
    assert '),28,354,' in source
    assert 'UiLabel("SIZING","Sizing: "' in source
    assert '),28,376,' in source
