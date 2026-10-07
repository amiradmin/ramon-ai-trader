"""Static regression checks for the MT5 Expert source."""

from pathlib import Path


EA = Path(__file__).resolve().parents[1] / "mt5" / "Ramon.mq5"


def test_model_url_allows_host_and_compose_service_only() -> None:
    source = EA.read_text(encoding="utf-8")

    assert '#property version "1.549"' in source
    assert 'url=="http://127.0.0.1:8012/decision"' in source
    assert 'url=="http://model:8012/decision"' in source
    assert "!IsAllowedModelUrl(ModelUrl)" in source
    assert 'StringFind(ModelUrl,"http://127.0.0.1:")!=0' not in source


def test_live_account_session_lock() -> None:
    source = EA.read_text(encoding="utf-8")

    assert '#property version "1.549"' in source
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

    assert 'RAMON AI TRADER  v0.54.9' in source
    assert 'UiButton("COPY","COPY DIAGNOSTIC"' in source
    assert 'UiButton("CLOSE","CLOSE TRADE"' in source
    assert "color DirectionColor(const string direction)" in source
    assert "string ForecastDirection()" in source
    assert "string EdgeDirection()" in source
    assert "string AttributionSummary()" in source
    assert "int AttributionVoteCount(const string direction)" in source
    assert '+" | BUY "+IntegerToString(AttributionVoteCount("BUY"))' in source
    assert '+" SELL "+IntegerToString(AttributionVoteCount("SELL"))' in source
    assert 'attribution_buy_votes,attribution_sell_votes,' in source
    assert 'UiLabel("SIGNAL"' in source and '28,154,clrWhite,9' in source
    assert 'cvals[0]=LastModelDecision' in source
    assert 'ccolors[3]=(edge_pass ? DirectionColor(dominant) : clrGold)' in source
    assert 'UiLabel("RISK_GATE",RiskGateText(),28,354' in source
    assert "AttributionAgreementCount" in source
    assert "AttributionConflictCount" in source
    assert 'UiLabel("ATTRIBUTION",AttributionSummary()' in source
    assert '"forecast_direction,edge_direction,intrabar_vote,trend_vote,"' in source
    assert "string CsvField(const string value)" in source
    assert "FileWriteString(handle,header);" in source
    assert "FileWriteString(handle,row);" in source
    assert 'attribution_agree,attribution_conflict,' in source
    assert '"sample_key","deal","position_id"' in source
    assert 'if(direction=="BUY")' in source
    assert "return clrLime;" in source
    assert 'if(direction=="SELL")' in source
    assert "return clrDeepSkyBlue;" in source
    assert 'cvals[3]=dominant+" "+DoubleToString(dominant_edge,2)' in source
    assert 'ccolors[3]=(edge_pass ? DirectionColor(dominant) : clrGold)' in source
    assert "sizing_color=DirectionColor(LastSizingSide)" in source
    assert "LastIntrabarConfirmed ? DirectionColor(LastIntrabarDirection) : clrGold" in source
    assert "LastAiTrendConfirmed ? DirectionColor(LastAiTrendDirection) : clrGold" in source
    assert "input bool ShowTPLevelsOnChart = true" in source
    assert 'const string TpUiPrefix = "RAMON_TP_"' in source
    assert "void DrawTPStageLevel(" in source
    assert "void UpdateTPStageObjects()" in source
    assert 'DrawTPStageLevel("TP1"' in source
    assert 'DrawTPStageLevel("TP2"' in source
    assert 'DrawTPStageLevel("TP3"' in source
    assert "OBJ_HLINE" in source
    assert "OBJ_TEXT" not in source
    assert "UpdateTPStageObjects();" in source
    assert "bool CloseManagedPositionFromDashboard()" in source
    assert '"manual_dashboard_close"' in source
    assert 'sparam==UiPrefix+"CLOSE"' in source
    assert "void OnChartEvent(" in source
    assert "CopyDiagnosticToClipboard()" in source
    assert "MQL_DLLS_ALLOWED" in source
    assert "SetClipboardData" in source
    assert 'JsonNumber(reply,"buy_edge",buy_edge)' in source
    assert 'JsonNumber(reply,"sell_edge",sell_edge)' in source
    assert 'JsonNumber(reply,"signal_strength",signal_strength)' in source
    assert 'cstats[3]=PassFail(edge_pass)' in source
    assert 'cstats[2]=PassFail(strength_pass)' in source


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

    assert '#property version "1.549"' in source
    assert "bool LiveExecutionReady(string &reason)" in source
    assert 'return (LiveExecutionReady(reason) ? "ARMED" : "BLOCKED")' in source
    assert 'if(!LiveExecutionReady(live_block_reason))' in source
    assert 'StatusLine=live_block_reason' in source
    assert 'Print("Ramon live BLOCKED: ConfirmMoneyUnitsPerUSD is false")' in source
    assert 'Print("Ramon live BLOCKED: account/server lock mismatch")' in source


def test_cent_account_dashboard_converts_units_to_usd() -> None:
    source = EA.read_text(encoding="utf-8")

    assert '#property version "1.549"' in source
    assert "input bool AccountIsCent = true" in source
    assert "string AccountTypeText()" in source
    assert "double AccountUnitsToUSD(const double units)" in source
    assert "bool MinimumLotExceedsRiskBudget()" in source
    assert '"AccountType: "+AccountTypeText()+" (configured)"' in source
    assert '"  BalanceUSDApprox: "+DoubleToString(AccountUnitsToUSD(AccountInfoDouble(ACCOUNT_BALANCE)),2)' in source
    assert '"  MinExecutableRiskUSD: "+DoubleToString(AccountUnitsToUSD(LastMinimumLotStopLossUnits),4)' in source
    assert '"TRADE BLOCKED: min lot > hard cap"' in source
    assert '"Min executable risk: $"' in source
    assert 'balance_usd_approx,' in source
    assert 'min_executable_risk_usd,' in source


def test_wait_display_distinguishes_strength_and_future_risk_block() -> None:
    source = EA.read_text(encoding="utf-8")

    assert '#property version "1.549"' in source
    assert "string RiskGateText()" in source
    assert '"WOULD BLOCK IF SIGNAL: min lot > hard cap"' in source
    assert '+"RiskGate: "+RiskGateText()+"\\n"' in source
    assert 'UiLabel("RISK_GATE",RiskGateText()' in source


def test_live_snapshots_re_evaluate_inside_same_m15_bar() -> None:
    source = EA.read_text(encoding="utf-8")

    assert '#property version "1.549"' in source
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

    assert '#property version "1.549"' in source
    assert "CopyRates(_Symbol,PERIOD_M1,0,4,micro)" in source
    assert '\\"micro_bars\\":[' in source
    assert 'JsonNumber(reply,"intrabar_confirmed",intrabar_confirmed)' in source
    assert 'JsonText(reply,"intrabar_direction",intrabar_direction)' in source
    assert 'cstats[4]=PassFail(LastIntrabarConfirmed)' in source
    assert "LastIntrabarConfirmed=(intrabar_confirmed>=0.5)" in source
    assert '"intrabar_reversal_up"' not in source  # decision reason belongs to Python core


def test_ai_trend_continuation_is_model_path_led() -> None:
    source = EA.read_text(encoding="utf-8")

    assert '#property version "1.549"' in source
    assert 'JsonNumber(reply,"ai_trend_confirmed",ai_trend_confirmed)' in source
    assert 'JsonText(reply,"ai_trend_direction",ai_trend_direction)' in source
    assert 'cstats[5]=PassFail(LastAiTrendConfirmed)' in source
    assert "LastAiTrendConfirmed=(ai_trend_confirmed>=0.5)" in source
    assert "iMA(" not in source
    assert "iRSI(" not in source


def test_minimum_lot_override_has_hard_cap() -> None:
    source = EA.read_text(encoding="utf-8")

    assert '#property version "1.549"' in source
    assert "input double RiskPerTradeUSD = 0.06" in source
    assert "input bool AllowMinLotRiskOverride = true" in source
    assert "const double MaxExecutableRiskUSD = 0.35" in source
    assert "bool MinimumLotOverrideEligible()" in source
    assert "bool RiskGateBlocked()" in source
    assert "double MaxExecutableRiskUnits()" in source
    assert 'return "PASS: MIN LOT OVERRIDE <= $"+DoubleToString(MaxExecutableRiskUSD,2)' in source
    assert 'StatusLine="TRADE BLOCKED: min lot > hard risk cap"' in source
    assert "return minimum;" in source
    assert "MaxExecutableRiskUSD>3.00" in source
    assert "MaxExecutableRiskUSD<EffectiveRiskPerTradeUSD()" in source
    assert 'max_executable_risk_usd,' in source
    assert 'min_lot_override_used,' in source


def test_small_profit_mode_is_opt_in() -> None:
    source = EA.read_text(encoding="utf-8")

    assert "input bool SmallOnlyMode = false" in source
    assert "input bool EnableSmallProfitTrades = false" in source


def test_dashboard_rows_are_not_overlapped() -> None:
    source = EA.read_text(encoding="utf-8")
    assert '28,200,clrWhite,9' in source  # role predictions
    assert '28,222,news_color,9' in source
    assert '28,244,clrWhite,9' in source  # account
    assert '28,266,clrWhite,9' in source  # balance
    assert '28,288,clrWhite,10' in source  # live P/L
    assert 'UiLabel("RISK_GATE",RiskGateText(),28,354' in source
    assert 'int tx=24, ty=410, tw=520, th=366, row_h=28;' in source
    assert 'UiButton("COPY","COPY DIAGNOSTIC",28,812,176,30)' in source
    assert 'UiButton("CLOSE","CLOSE TRADE",218,812,110,30)' in source


def test_tp_stage_crossings_lock_profit_on_every_tick() -> None:
    source = EA.read_text(encoding="utf-8")

    assert "void OnTick()" in source
    assert "ObserveTPStageCrossingsOnTick();" in source
    assert "void MarkTPStageReached(" in source
    assert "bool ProtectReachedTPStage(" in source
    assert "Trade.PositionModify(ticket,desired_sl,current_tp)" in source
    assert 'TPStageLockStatus="WAIT_BROKER_DISTANCE"' in source
    assert 'TPStageLockStatus=(TPStage>=2 ? "TP2_LOCKED" : "TP1_LOCKED")' in source
    assert '"TPStageLock: "+TPStageLockStatus' in source
    assert '"  OnTickCrossing=YES\\n"' in source


def test_main_early_profit_lock_is_r_based_and_never_loosens_manual_sl() -> None:
    source = EA.read_text(encoding="utf-8")

    assert "input bool EnableEarlyProfitLock = true" in source
    assert "EarlyProfitLockActivation1R = 0.40" in source
    assert "EarlyProfitLockActivation2R = 0.70" in source
    assert "EarlyProfitLockStage1R = 0.05" in source
    assert "EarlyProfitLockStage2R = 0.20" in source
    assert 'GlobalVariableSet(TPPlanGlobalKey(sample_key,"ISL"),initial_sl);' in source
    assert "bool LoadEarlyProfitPlan(" in source
    assert "bool ProtectEarlyProfit(" in source
    assert "risk_distance=MathAbs(entry-initial_sl)" in source
    assert "ProtectEarlyProfit(ticket,tick);" in source
    assert "current_sl>=desired_sl-point*0.5" in source
    assert "current_sl<=desired_sl+point*0.5" in source
    assert '"EARLY_WAIT_BROKER_DISTANCE"' in source
    assert '"EARLY_BE_PLUS_LOCKED"' in source
    assert '"EARLY_02R_LOCKED"' in source


def test_shadow_and_learning_items_are_starred_in_ui() -> None:
    source = EA.read_text(encoding="utf-8")

    assert '"*SHADOW* | "+LastShadowRegimeLabel' in source
    assert '"*LEARNING*"' in source
    assert '" R"+role_mark+":"' in source
    assert '" E"+role_mark+":"' in source
    assert '" N"+role_mark+":"' in source
    assert '" M"+role_mark+":"' in source
    assert '" SL"+role_mark+":"' in source
    assert 'LastNewsModelReady ? "READY" : "*LEARNING*"' in source
    assert 'LastTargetStructureReady ? "READY" : "*LEARNING*"' in source
    assert '"TargetLearning*: "' in source
    assert '"  *ShadowRiskP: "' in source
    assert '"* = SHADOW / LEARNING / COLLECTING' in source


def test_role_model_dashboard_and_response_fields() -> None:
    source = EA.read_text(encoding="utf-8")

    assert '#property version "1.549"' in source
    assert 'JsonText(reply,"base_decision",base_decision)' in source
    assert 'JsonNumber(reply,"ensemble_ready",ensemble_ready)' in source
    assert 'JsonNumber(reply,"regime_probability",regime_probability)' in source
    assert 'JsonNumber(reply,"entry_probability",entry_probability)' in source
    assert 'JsonNumber(reply,"news_probability",news_probability)' in source
    assert 'JsonNumber(reply,"meta_probability",meta_probability)' in source
    assert 'JsonNumber(reply,"risk_model_ready",risk_model_ready)' in source
    assert 'JsonNumber(reply,"risk_probability",risk_probability)' in source
    assert 'JsonNumber(reply,"risk_multiplier",risk_multiplier)' in source
    assert "double EffectiveRiskPerTradeUSD()" in source
    assert 'JsonText(reply,"news_source",news_source)' in source
    assert 'JsonText(reply,"news_event_title",news_event_title)' in source
    assert 'UiLabel("ROLE_MODELS",(LastRoleShadow ? "*SHADOW* | "' in source
    assert 'UiLabel("NEWS","NEWS "' in source
    assert "LastEnsembleReady=(ensemble_ready>=0.5)" in source
    assert 'base_decision,base_reason,ensemble_ready,ensemble_active,' in source


def test_daily_trade_cap_is_400_by_default() -> None:
    source = EA.read_text(encoding="utf-8")

    assert "input int MaxTradesPerDay = 400;" in source
    assert "today>=MaxTradesPerDay" in source


def test_decision_webrequest_is_prioritized_over_trade_sync() -> None:
    source = EA.read_text(encoding="utf-8")

    on_timer = source.split("void OnTimer()", 1)[1].split("void OnTradeTransaction(", 1)[0]
    idle_guard = """if(LastDecisionRequestTime>0
      && now-LastDecisionRequestTime<SnapshotIntervalSeconds)
   {
      // Never issue trade telemetry immediately before a live model decision."""
    assert idle_guard in on_timer
    assert on_timer.index("SyncClosedTrades();") < on_timer.index("LastDecisionRequestTime=now;")
    assert "SyncClosedTrades();\n   datetime closed=" not in on_timer


def test_event_time_telemetry_is_durable_and_does_not_invent_historical_offsets() -> None:
    source = EA.read_text(encoding="utf-8")
    payload = source.split('bool ClosedTradePayload(', 1)[1].split('void SyncClosedTrades()', 1)[0]
    assert 'TimeGMT()' not in payload  # historical replay must read event-time metadata
    assert 'ReadDealTelemetry(opening_deal' in payload
    assert 'ReadDealTelemetry(closing_deal' in payload
    assert 'DEAL_FEE' in payload
    assert 'RecordDealTelemetry(Trade.ResultDeal(),"maximum_hold_bars")' in source
    callback = source.split('void OnTradeTransaction(', 1)[1].split('void OnChartEvent', 1)[0]
    assert 'RecordDealTelemetry(trans.deal);' in callback
    assert 'FILE_COMMON' in source.split('void RecordDealTelemetry(', 1)[1].split('bool ClosedTradePayload(', 1)[0]


def test_v038_tp_stage_management_is_live_but_broker_tp_stays_legacy() -> None:
    source = EA.read_text(encoding="utf-8")

    assert '#property version "1.549"' in source
    assert 'JsonNumber(reply,"target_learning_active",target_learning_active)' in source
    assert 'JsonNumber(reply,"target_tp1",target_tp1)' in source
    assert 'JsonNumber(reply,"target_tp2",target_tp2)' in source
    assert 'JsonNumber(reply,"target_tp3",target_tp3)' in source
    assert '"ExecutionTargetMode: MAIN_TP3_BROKER_FAILSAFE_WHEN_VALID\\n"' in source
    # Valid MAIN stage plans promote TP3 to the broker-side fail-safe target.
    assert 'double target=NormalizeDouble(entry+(decision=="BUY" ? target_distance : -target_distance),_Digits);' in source
    assert "bool main_tp_plan_valid=(" in source
    assert "ValidDirectionalTargets(decision,entry,LastTargetTP1,LastTargetTP2,LastTargetTP3)" in source
    assert "target=broker_tp3;" in source
    assert "PersistTPPlan(LastSampleKey,decision,entry,stop," in source
    assert "LastTargetTP1,LastTargetTP2,LastTargetTP3);" in source
    assert "bool ManageTPStages(const ulong ticket)" in source
    assert 'RecordDealTelemetry(Trade.ResultDeal(),"tp3_stage_exit")' in source
    assert '"tp1_stall_exit"' in source
    assert '"tp2_stall_exit"' in source
    assert "if(ManageTPStages(ticket))" in source
    assert "TPStageWeakSnapshotsRequired = 2" in source
    assert "TP1GraceSeconds = 60" in source
    assert "TP2GraceSeconds = 30" in source


def test_v035_profit_protection_is_active_and_auditable() -> None:
    source = EA.read_text(encoding="utf-8")

    assert '#property version "1.549"' in source
    assert "input bool EnableProfitProtection = true" in source
    assert "const double ProfitProtectionFallbackActivationUnits = 7.0" in source
    assert "const double ProfitProtectionActivationMinUnits = 5.0" in source
    assert "const double ProfitProtectionActivationMaxUnits = 9.0" in source
    assert "const double ProfitProtectionActivationRiskFraction = 0.60" in source
    assert "const double ProfitProtectionGivebackMinUnits = 3.0" in source
    assert "const double ProfitProtectionGivebackMaxUnits = 5.0" in source
    assert "const double ProfitProtectionGivebackFraction = 0.60" in source
    assert "SelectDynamicProfitProtectionThresholds(ticket);" in source
    assert "ManagedPositionInitialRiskUnits" in source
    assert "mode=DYNAMIC" in source
    assert "if(EnableProfitProtection && ProfitProtectionShadowTriggered)" in source
    assert 'Trade.PositionClose(ticket,MaxDeviationPoints)' in source
    assert 'RecordDealTelemetry(Trade.ResultDeal(),"profit_protection")' in source
    assert '"ProfitProtection: "+(EnableProfitProtection ? "ACTIVE" : "OFF")' in source
    assert "ProfitProtectionGivebackMinUnits>=ProfitProtectionActivationMinUnits" in source
    assert "ProfitProtectionActivationUnits*ProfitProtectionGivebackFraction" in source
    assert "ProfitProtectionInitialRiskUnits*ProfitProtectionActivationRiskFraction" in source


def test_early_reversal_exit_is_shadow_only() -> None:
    source = EA.read_text(encoding="utf-8")

    assert "input bool ObserveEarlyReversalExit = true" in source
    assert "input double EarlyReversalMinPeakUnits = 2.0" in source
    assert "input double EarlyReversalGivebackUnits = 6.0" in source
    assert "input double EarlyReversalMaxCurrentUnits = 0.0" in source
    assert "EarlyReversalShadowTriggered=true;" in source
    assert '"Ramon EARLY REVERSAL SHADOW ticket="' in source
    shadow = source.split("if(ObserveEarlyReversalExit", 1)[1].split("bool IsRangeTradeComment", 1)[0]
    assert "Trade.PositionClose" not in shadow
    assert "!LastIntrabarConfirmed" in shadow
    assert "!LastAiTrendConfirmed" in shadow


def test_v049_small_loss_reduction_is_small_only() -> None:
    source = EA.read_text(encoding="utf-8")

    assert '#property version "1.549"' in source
    assert "const double EarlyAdverseRiskFraction = 0.60" in source
    assert "const double SmallEarlyAdverseRiskFraction = 0.50" in source
    assert "? SmallEarlyAdverseRiskFraction : EarlyAdverseRiskFraction" in source
    assert "int SmallLossClosedThisSignalBar(const datetime bar_time)" in source
    assert 'StatusLine="Second SMALL blocked: first attempt lost this M15 bar"' in source
    assert "if(small_entries_on_bar>0)" in source
    assert "net<0.0" in source
    assert "Small entries per signal bar:" in source


def test_v050_small_dynamic_target_is_shadow_only() -> None:
    source = EA.read_text(encoding="utf-8")

    assert '#property version "1.549"' in source
    assert "const double SmallProfitTargetUnits = 4.0" in source
    assert "const double ShadowSmallStrongTargetUnits = ShadowSmallTP2Units" in source
    assert "ShadowSmallStrongTargetCandidate=(" in source
    assert "&& intrabar_support" in source
    assert "&& trend_support" in source
    assert "&& edge_support" in source
    assert "ShadowSmallTargetUnits=ShadowSmallStrongTargetUnits;" in source
    assert '"*ShadowExecutionEffect: NONE\\n"' in source
    # Actual broker target must still use the fixed live 4-cent target.
    target_fn = source.split("bool SmallProfitTarget(", 1)[1].split("bool SmallProfitStop(", 1)[0]
    assert "SmallProfitTargetUnits/unit_gain" in target_fn
    assert "ShadowSmallTargetUnits" not in target_fn


def test_v050_small_three_stage_tp_shadow_is_observational() -> None:
    source = EA.read_text(encoding="utf-8")

    assert "const double ShadowSmallTP1Units = 2.0" in source
    assert "const double ShadowSmallTP2Units = 2.5" in source
    assert "const double ShadowSmallTP3Units = 3.0" in source
    assert 'ShadowSmallTPPlan = "TP1=2.00 TP2=2.50 TP3=3.00"' in source
    assert "WOULD_HOLD_FOR_TP2" in source
    assert "WOULD_CLOSE_AT_TP1" in source
    assert "WOULD_HOLD_FOR_TP3" in source
    assert "WOULD_CLOSE_AT_TP2" in source
    assert "WOULD_CLOSE_AT_TP3" in source
    # The experiment must not alter live order submission or broker target.
    assert '"*ShadowExecutionEffect: NONE\\n"' in source
    target_fn = source.split("bool SmallProfitTarget(", 1)[1].split("bool SmallProfitStop(", 1)[0]
    assert "SmallProfitTargetUnits" in target_fn
    assert "ShadowSmallTP" not in target_fn


def test_v051_small_entry_quality_filter_requires_edge_and_directional_confirmation() -> None:
    source = EA.read_text(encoding="utf-8")

    assert '#property version "1.549"' in source
    fn = source.split("bool SmallProfitCandidate(", 1)[1].split("bool SmallProfitTarget(", 1)[0]
    assert 'directional_edge<LastMinimumEdge' in fn
    assert 'LastIntrabarConfirmed' in fn
    assert 'LastIntrabarDirection==direction' in fn
    assert 'LastAiTrendConfirmed' in fn
    assert 'LastAiTrendDirection==direction' in fn
    assert 'if(!intrabar_support && !trend_support)' in fn
    assert 'SMALL_FILTER_EDGE_FAIL' in fn
    assert 'SMALL_FILTER_CONFIRM_FAIL' in fn
    assert 'SMALL_FILTER_PASS_INTRABAR' in fn
    assert 'SMALL_FILTER_PASS_TREND' in fn
    assert 'SMALL_FILTER_PASS_BOTH' in fn
    assert 'SmallEntryQualityFilter: ACTIVE' in source


def test_v051_small_filter_does_not_change_main_risk_or_small_targets() -> None:
    source = EA.read_text(encoding="utf-8")

    assert "input double RiskPerTradeUSD = 0.06" in source
    assert "const double SmallProfitTargetUnits = 4.0" in source
    assert "const double SmallProfitMaxLossUnits = 2.0" in source
    assert "const double SmallEarlyAdverseRiskFraction = 0.50" in source
    assert "const int SmallProfitMaxEntriesPerSignalBar = 2" in source


def test_v052_main_fast_profit_is_main_only_and_conservative() -> None:
    source = EA.read_text(encoding="utf-8")

    assert '#property version "1.549"' in source
    assert "input bool EnableMainFastProfit = false" in source
    assert "const int MainFastProfitMinAgeBars = 2" in source
    assert "const double MainFastProfitMinProfitUnits = 0.20" in source
    assert "const double MainFastProfitMinProgressToTP1 = 0.35" in source
    assert "const int MainFastProfitWeakSnapshotsRequired = 2" in source

    fn = source.split("bool ManageMainFastProfit(", 1)[1].split("void ResetProfitProtectionState()", 1)[0]
    assert "!EnableMainFastProfit || SmallOnlyMode" in fn
    assert "age<MainFastProfitMinAgeBars" in fn
    assert "current_units<MainFastProfitMinProfitUnits" in fn
    assert "MainFastProfitProgress>=MainFastProfitMinProgressToTP1" in fn
    assert "LastModelSnapshotTime" in fn
    assert "LastModelDecision==direction" in fn
    assert "LastIntrabarConfirmed && LastIntrabarDirection==direction" in fn
    assert "LastAiTrendConfirmed && LastAiTrendDirection==direction" in fn
    assert "MainFastProfitWeakSnapshots++" in fn
    assert 'RecordDealTelemetry(Trade.ResultDeal(),"main_fast_profit")' in fn


def test_main_manages_early_adverse_exit_after_tp_stages() -> None:
    source = EA.read_text(encoding="utf-8")
    manager = source.split("void ManageOpenPosition()", 1)[1].split("void OnTimer()", 1)[0]
    main_branch = manager.split("if(!SmallOnlyMode)", 1)[1].split("if(EnforceSmallPositionRiskCap(ticket))", 1)[0]

    assert "ManageTPStages(ticket)" in main_branch
    assert "ManageMainFastProfit(ticket,opened)" in main_branch
    assert "ObserveOpenPositionProfit(ticket)" not in main_branch
    assert "ManageEarlyAdverseExit(ticket,opened)" in main_branch
    assert main_branch.index("ManageTPStages(ticket)") < main_branch.index("ManageEarlyAdverseExit(ticket,opened)")
    assert "ProfitProtectionShadowTriggered" not in main_branch
    assert "ManageMainMaximumHold(ticket,opened)" in main_branch
    assert 'StatusLine="Managed MAIN OPEN | TP stages + EARLY ADVERSE + MAX HOLD"' in main_branch
    assert "MainExitMode: TP1_TP2_TP3 + EARLY_ADVERSE" in source


def test_range_main_quick_five_cent_target_contract():
    source = Path("mt5/Ramon.mq5").read_text(encoding="utf-8")
    assert "const double RangeMainTargetUnits = 5.0;" in source
    assert "const double RangeMainMaxLossUnits = 5.0;" in source
    assert "RangeMainProfitTarget" in source
    assert "Range MAIN blocked: boundary SL risk > 5c" in source
    assert "Range MAIN blocked: 5c TP not inside midpoint" in source


def test_max_executable_risk_cap_is_035():
    source = Path("mt5/Ramon.mq5").read_text(encoding="utf-8")
    assert "double MaxExecutableRiskUSD = 0.35;" in source
    assert "ReadControlRiskCap();" in source
