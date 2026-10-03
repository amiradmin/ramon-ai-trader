#property strict
#property version "1.554"
#property description "Independent Chronos-2 XAUUSD_l M15 bot; local model server required."

#include <Trade/Trade.mqh>

#define RAMON_GMEM_MOVEABLE 0x0002
#define RAMON_CF_UNICODETEXT 13

#import "user32.dll"
int  OpenClipboard(long hwnd);
int  EmptyClipboard();
int  CloseClipboard();
long SetClipboardData(uint format,long hMem);
#import "kernel32.dll"
long GlobalAlloc(uint flags,ulong bytes);
long GlobalLock(long hMem);
int  GlobalUnlock(long hMem);
long GlobalFree(long hMem);
long lstrcpyW(long dst,const string src);
#import

input string TradeSymbol = "XAUUSD_l";
input string RequiredServerText = "LiteFinance";
input bool AutoLockCurrentAccount = true; // Bind this EA session to the account active at OnInit.
input long AllowedAccountLogin = 0; // Used only when AutoLockCurrentAccount=false.
input bool EnableRangeMain = true; // Explicit cent-account range-reversal trial on MAIN.
const double RangeMainTargetUnits = 5.0; // Quick RANGE take-profit: USD 0.05 on 100 units/USD.
const double RangeMainMaxLossUnits = 5.0; // RANGE entries must keep broker SL risk <= USD 0.05.
input bool EnableLiveTrading = false;
input bool SmallOnlyMode = false; // Attach second EA instance on another M15 chart for parallel SMALL trades.
const ulong PrimaryMagicNumber = 26092212;
const ulong SmallProfitMagicNumber = 26092213;
input string ModelUrl = "http://127.0.0.1:8012/decision";
input double MoneyUnitsPerUSD = 100.0; // CENT account: 100 account units = 1 USD.
input bool AccountIsCent = true; // Configured account mode; MT5 ACCOUNT_CURRENCY alone cannot identify CENT.
input bool ConfirmMoneyUnitsPerUSD = false; // Must be true before live trading can arm.
input string ExpectedAccountCurrency = ""; // Optional exact ACCOUNT_CURRENCY check when non-empty.
input double RiskPerTradeUSD = 0.06; // Preferred sizing budget.
input bool AllowMinLotRiskOverride = true; // Permit minimum volume within the fixed executable risk cap.
const double MaxExecutableRiskUSD = 0.35; // Hard fixed cap; MT5 chart inputs cannot override this value.
input int MaxSpreadPoints = 50;
input int MaxTradesPerDay = 400;
input int MaximumHoldBars = 4;
input bool EnableAccountLossLimits = false; // Entry-only account guard; configure and validate before activation.
input double DailyLossLimitPercent = 0.0; // 0 disables this limit; include realized costs and floating loss.
input double MaximumEquityDrawdownPercent = 0.0; // 0 disables; peak persists per account/server.
input bool EnableMainFastProfit = false; // Optional MAIN-only early profit exit; off by default until explicitly enabled.
const int MainFastProfitMinAgeBars = 2; // Evaluate only after at least 2 M15 bars (~30 min).
const double MainFastProfitMinProfitUnits = 0.20; // Never close a losing MAIN trade through this feature.
const double MainFastProfitMinProgressToTP1 = 0.35; // Below 35% of entry->TP1 after 2 bars is considered slow.
const int MainFastProfitWeakSnapshotsRequired = 2; // Require repeated weak 30s snapshots.
input bool EnableSmallProfitTrades = false; // SMALL is out of scope unless explicitly enabled on a separate instance.
const double SmallProfitTargetUnits = 4.0; // CENT account units = USD 0.04 when MoneyUnitsPerUSD=100.
const double ShadowSmallTP1Units = 2.0; // Observe-only SMALL stage 1.
const double ShadowSmallTP2Units = 2.5; // Observe-only SMALL stage 2.
const double ShadowSmallTP3Units = 3.0; // Observe-only SMALL stage 3.
const double ShadowSmallStrongTargetUnits = ShadowSmallTP2Units; // Current strong-entry candidate.
const double SmallProfitMaxLossUnits = 2.0; // Broker SL for new small entries: at most USD 0.02 on a 100-units/USD account.
const double SmallProfitProtectionActivationUnits = 1.0;
const double SmallProfitProtectionGivebackUnits = 0.5;
const double SmallProfitMaxRiskUSD = 0.02; // Independent ceiling for new small entries.
const int SmallProfitMaxEntriesPerSignalBar = 2; // Sequential entries only; an open position still blocks another entry.
const int SmallProfitMaximumHoldBars = 3;
input bool EnableProfitProtection = true; // Close a managed position after an armed profit giveback.
const double ProfitProtectionFallbackActivationUnits = 7.0; // Used only if live position risk cannot be reconstructed.
const double ProfitProtectionActivationMinUnits = 5.0;
const double ProfitProtectionActivationMaxUnits = 9.0;
const double ProfitProtectionActivationRiskFraction = 0.60; // Activation scales with the position's initial SL risk.
const double ProfitProtectionGivebackMinUnits = 3.0;
const double ProfitProtectionGivebackMaxUnits = 5.0;
const double ProfitProtectionGivebackFraction = 0.60; // Giveback scales with the selected activation threshold.
const bool EnableTPStageManagement = true; // Live TP1/TP2/TP3 state machine.
input bool EnableEarlyProfitLock = true; // MAIN: tighten broker SL before TP1 after measured R progress.
const double EarlyProfitLockActivation1R = 0.40;
const double EarlyProfitLockActivation2R = 0.70;
const double EarlyProfitLockStage1R = 0.05; // Breakeven-plus buffer for costs/slippage.
const double EarlyProfitLockStage2R = 0.20;
const int TPStageWeakSnapshotsRequired = 2; // Consecutive weak 30s snapshots before an early stage exit.
const int TP1GraceSeconds = 60; // Give TP1->TP2 continuation one minute before weakness exit.
const int TP2GraceSeconds = 30; // Shorter grace after TP2.
const double TP1HealthyProgressFraction = 0.35; // Progress toward TP2 considered healthy.
const double TP1RetraceFraction = 0.15; // Exit if price gives back this fraction of TP1->TP2 below TP1.
const double TP2RetraceFraction = 0.20; // Exit if price gives back this fraction of TP2->TP3 below TP2.
input bool ObserveEarlyReversalExit = true; // Shadow-only: record damaged trades without closing them.
input double EarlyReversalMinPeakUnits = 2.0; // Require at least this much favorable excursion first.
input double EarlyReversalGivebackUnits = 6.0; // Require this much giveback from peak.
input double EarlyReversalMaxCurrentUnits = 0.0; // Trigger only after the trade has returned to breakeven/loss.
const bool EnableEarlyAdverseExit = true; // Active loss reduction: exit only after material loss plus repeated signal failure.
const double EarlyAdverseRiskFraction = 0.60; // MAIN: arm at 60% of reconstructed initial SL risk.
const double SmallEarlyAdverseRiskFraction = 0.50; // SMALL: evaluate earlier at 50% of risk (~1c on a 2c stop).
const int EarlyAdverseWeakSnapshotsRequired = 2; // Require two distinct model snapshots with no support.
const int EarlyAdverseMinAgeSeconds = 120; // Give a new trade two minutes before adverse-exit evaluation.
input int RequestTimeoutMs = 4000;
input int SnapshotIntervalSeconds = 30; // Re-evaluate fresh Bid/Ask inside the same M15 bar.
input int MaxDeviationPoints = 30;
input ulong MagicNumber = 26092212;
input bool WriteDiagnosticFile = true;
input string DiagnosticFileName = "Ramon_Diagnostic.txt";
input bool ShowDashboard = true;
input bool ShowTPLevelsOnChart = true; // Draw active MAIN TP1/TP2/TP3 levels and stage state on the chart.
input bool EnableClipboardButton = true;
input bool WriteCsvLogs = true;
input string SignalCsvFileName = "Ramon_Signals.csv";
input string TradeCsvFileName = "Ramon_Trades.csv";
input bool EnableImprovementShadowPack = true; // Observe-only: never blocks/opens/closes/resizes trades.
input string ShadowCsvFileName = "Ramon_Shadow_Improvements.csv";

CTrade Trade;
string AccountLossLimitStatus = "DISABLED";
datetime LastDecisionRequestTime = 0;
datetime LastModelSnapshotTime = 0;
datetime LastEntrySignalBar = 0;
int LastSmallEntriesOnSignalBar = 0;
string StatusLine = "Starting";
string LastModelDecision = "NONE";
string LastModelReason = "NONE";
string LastSampleKey = "";
bool LastSampleSaved = false;
string LastBundleId = "";
string TradeLearningStatus = "COLLECTING REAL TRADES";
datetime LastTradeSync = 0;
ulong SyncedTradeIds[];
string LastBaseDecision = "NONE";
string LastBaseReason = "NONE";
bool LastEnsembleReady = false;
bool LastEnsembleActive = false;
double LastRegimeProbability = -1.0;
bool LastRoleShadow = false;
string LastShadowRegimeLabel = "UNAVAILABLE";
double LastShadowRiskProbability = -1.0;
double LastEntryProbability = -1.0;
double LastNewsProbability = -1.0;
double LastMetaProbability = -1.0;
bool LastRiskModelReady = false;
double LastRiskProbability = -1.0;
double LastRiskMultiplier = 1.0;
bool LastNewsSourceReady = false;
bool LastNewsModelReady = false;
string LastForecastModelHandler = "chronos-2-small";
string LastForecastShadowModelHandler = "OFF";
bool LastTimesFMReady = false;
string LastTimesFMDirection = "UNAVAILABLE";
double LastTimesFMLow = -1.0;
double LastTimesFMMedian = -1.0;
double LastTimesFMHigh = -1.0;
double LastTimesFMMoveAtr = -1.0;
bool LastTimesFMAgreesChronos = false;
string LastMarketState = "UNAVAILABLE";
string LastMarketStateRoute = "UNAVAILABLE";
string LastMarketStatePolicy = "UNAVAILABLE";
string LastRiskTarget = "none";
string LastRegimeModelHandler = "Ramon/BinaryLogisticModel";
string LastEntryModelHandler = "Ramon/BinaryLogisticModel";
string LastNewsModelHandler = "Ramon/BinaryLogisticModel";
string LastMetaModelHandler = "Ramon/BinaryLogisticModel";
string LastRiskModelHandler = "Ramon/BinaryLogisticModel";
string LastNewsSourceHandler = "ForexFactoryNewsProvider";
string LastMarketStateHandler = "market-state-v1";
string LastTargetModelHandler = "Ramon/TargetStructure";
string LastAnomalyModelHandler = "OFF";
string LastNewsSentimentModelHandler = "OFF";
bool LastMomentShadowReady = false;
double LastMomentAnomalyScore = -1.0;
double LastMomentAnomalyRatio = -1.0;
string LastMomentAnomalyLabel = "UNAVAILABLE";
bool LastFinbertShadowReady = false;
string LastFinbertSentimentLabel = "UNAVAILABLE";
double LastFinbertDirectionalScore = 0.0;
bool LastMomentLiveActive = false;
bool LastMomentLiveFresh = false;
bool LastMomentLiveVeto = false;
double LastMomentLiveThreshold = 2.0;
bool LastFinbertLiveActive = false;
bool LastFinbertLiveVeto = false;
double LastFinbertLiveThreshold = 0.35;
double LastNewsSourceAgeSeconds = -1.0;
string LastNewsSource = "NONE";
string LastNewsEventTitle = "NONE";
string LastNewsEventCountry = "NONE";
string LastNewsEventImpact = "NONE";
datetime LastNewsEventTime = 0;
double LastNewsEventDeltaMinutes = 0.0;
datetime LastSignalBarTime = 0;
double LastSignalBid = 0.0;
double LastSignalAsk = 0.0;
double LastForecastLow = 0.0;
double LastForecast = 0.0;
double LastForecastHigh = 0.0;
double LastAtr = 0.0;
double LastEdge = 0.0;
double LastBuyEdge = 0.0;
double LastSellEdge = 0.0;
double LastMinimumEdge = 0.0;
double LastUncertainty = 0.0;
double LastSignalStrength = 0.0;
double LastMinimumStrength = 0.20;
bool LastIntrabarConfirmed = false;
string LastIntrabarDirection = "NONE";
double LastIntrabarMoveAtr = 0.0;
double LastIntrabarReboundAtr = 0.0;
double LastIntrabarMinStrength = 0.05;
double LastIntrabarMinMoveAtr = 0.06;
double LastIntrabarMinReboundAtr = 0.08;
bool LastAiTrendConfirmed = false;
string LastAiTrendDirection = "NONE";
double LastAiTrendScore = 0.0;
double LastAiTrendMoveAtr = 0.0;
double LastAiTrendConsistency = 0.0;
double LastTrendMinPathAtr = 0.15;
double LastTrendMinConsistency = 0.75;
double LastTrendMinEdgeFraction = 0.25;
double LastTrendMinMicroMoveAtr = 0.03;
string LastSizingSide = "NONE";
double LastPlannedVolume = 0.0;
double LastEstimatedStopLossUnits = 0.0;
double LastMinimumLotStopLossUnits = 0.0;
double LastRiskBudgetUnits = 0.0;
bool LastMinLotOverrideUsed = false;
string PendingSizingSampleKey = "";
double PendingSizingRiskBudgetUnits = 0.0;
double PendingSizingPlannedVolume = 0.0;
double PendingSizingMinLotSLUnits = 0.0;
bool PendingSizingOverrideUsed = false;
double PendingSizingMaxExecutableRiskUSD = 0.0;
double PendingSizingMoneyUnitsPerUSD = 0.0;
double LastStopDistance = 0.0;
double LastTargetDistance = 0.0;
bool LastTargetLearningActive = false;
bool LastTargetStructureReady = false;
string LastTargetMethod = "NONE";
string LastTargetDirection = "NONE";
double LastTargetImpulseStart = 0.0;
double LastTargetImpulseEnd = 0.0;
double LastTargetImpulseRange = 0.0;
double LastTargetImpulseAtr = 0.0;
double LastTargetTP1 = 0.0;
double LastTargetTP2 = 0.0;
double LastTargetTP3 = 0.0;
double LastLegacyTargetPrice = 0.0;
int LastModelSpreadPoints = 0;
long LockedAccountLogin = 0;
string LockedAccountServer = "";
string LastCopyStatus = "Ready";
string LastCloseStatus = "Ready";
const string UiPrefix = "RAMON_UI_";
const string TpUiPrefix = "RAMON_TP_";
ulong ProfitProtectionTicket = 0;
double ProfitProtectionPeakUnits = 0.0;
double ProfitProtectionCurrentUnits = 0.0;
double ProfitProtectionGivebackNowUnits = 0.0;
double ProfitProtectionInitialRiskUnits = 0.0;
double ProfitProtectionActivationUnits = ProfitProtectionFallbackActivationUnits;
double ProfitProtectionGivebackUnits = 4.2;
bool ProfitProtectionArmed = false;
bool ProfitProtectionShadowTriggered = false;
datetime ProfitProtectionShadowTriggerTime = 0;
bool EarlyReversalShadowTriggered = false;
datetime EarlyReversalShadowTriggerTime = 0;
ulong EarlyAdverseTicket = 0;
double EarlyAdverseInitialRiskUnits = 0.0;
double EarlyAdverseTriggerLossUnits = 0.0;
double EarlyAdverseAppliedRiskFraction = 0.0;
int EarlyAdverseWeakSnapshots = 0;
datetime EarlyAdverseLastDecisionTime = 0;
bool EarlyAdverseTriggered = false;
ulong TPStagePositionIdentifier = 0;
string TPStageSampleKey = "";
string TPStageDirection = "NONE";
double TPStageTP1 = 0.0;
double TPStageTP2 = 0.0;
double TPStageTP3 = 0.0;
int TPStage = 0;
datetime TPStageHitTime = 0;
int TPStageWeakSnapshots = 0;
datetime TPStageLastDecisionTime = 0;
double TPStageProgress = 0.0;
string TPStageStatus = "INACTIVE";
double TPStageLockedSL = 0.0;
string TPStageLockStatus = "INACTIVE";
bool MarketClosedExitPause = false;
datetime MarketClosedExitPauseTickTime = 0;
ulong MarketClosedExitPauseTicket = 0;
ulong MainFastProfitTicket = 0;
int MainFastProfitWeakSnapshots = 0;
datetime MainFastProfitLastDecisionTime = 0;
double MainFastProfitProgress = 0.0;
string MainFastProfitStatus = "INACTIVE";

// v0.50 improvement pack: telemetry only. These values MUST NOT be used by execution gates.
bool ShadowBuyCaution = false;
bool ShadowSellCaution = false;
double ShadowRiskMultiplier = 1.0;
double ShadowSmallTargetUnits = SmallProfitTargetUnits;
bool ShadowSmallStrongTargetCandidate = false;
int ShadowSmallTPStage = 0;
string ShadowSmallTPPlan = "TP1=2.00 TP2=2.50 TP3=3.00";
string ShadowSmallTPNextAction = "NONE";
bool ShadowDeadTrade = false;
double ShadowDeadTradePeakR = 0.0;
double ShadowDeadTradeCurrentR = 0.0;
string ShadowReason = "NONE";

bool IsAllowedModelUrl(const string url)
{
   return (
      url=="http://127.0.0.1:8012/decision"
      || url=="http://model:8012/decision"
   );
}

bool AccountLockHealthy()
{
   if(LockedAccountLogin<=0)
      return false;
   if(AccountInfoInteger(ACCOUNT_LOGIN)!=LockedAccountLogin)
      return false;
   string current_server=AccountInfoString(ACCOUNT_SERVER);
   if(current_server!=LockedAccountServer)
      return false;
   if(StringLen(RequiredServerText)>0 && StringFind(current_server,RequiredServerText)<0)
      return false;
   return true;
}

bool CurrencyCheckHealthy()
{
   if(StringLen(ExpectedAccountCurrency)==0)
      return true;
   return AccountInfoString(ACCOUNT_CURRENCY)==ExpectedAccountCurrency;
}

bool LiveExecutionReady(string &reason)
{
   reason="";
   if(!EnableLiveTrading)
   {
      reason="Live trading disabled";
      return false;
   }
   if(!ConfirmMoneyUnitsPerUSD)
   {
      reason="BLOCKED: confirm MoneyUnitsPerUSD";
      return false;
   }
   if(!CurrencyCheckHealthy())
   {
      reason="BLOCKED: account currency mismatch";
      return false;
   }
   if(!AccountLockHealthy())
   {
      reason="BLOCKED: account/server lock mismatch";
      return false;
   }
   return true;
}

string LiveStateText()
{
   if(!EnableLiveTrading)
      return "DISARMED";
   string reason="";
   return (LiveExecutionReady(reason) ? "ARMED" : "BLOCKED");
}

bool ManagedPosition(ulong &ticket,datetime &opened)
{
   ticket=0;
   opened=0;
   for(int i=PositionsTotal()-1;i>=0;i--)
   {
      ulong candidate=PositionGetTicket(i);
      if(candidate==0 || !PositionSelectByTicket(candidate))
         continue;
      if(PositionGetString(POSITION_SYMBOL)!=_Symbol)
         continue;
      if((ulong)PositionGetInteger(POSITION_MAGIC)!=MagicNumber)
         continue;
      ticket=candidate;
      opened=(datetime)PositionGetInteger(POSITION_TIME);
      return true;
   }
   return false;
}

bool IsSmallProfitPosition(const ulong ticket)
{
   if(ticket==0 || !PositionSelectByTicket(ticket)) return false;
   return SmallOnlyMode
      && (ulong)PositionGetInteger(POSITION_MAGIC)==SmallProfitMagicNumber;
}

bool OtherPositionOnSymbol()
{
   ulong peer_magic=(SmallOnlyMode ? PrimaryMagicNumber : SmallProfitMagicNumber);
   for(int i=PositionsTotal()-1;i>=0;i--)
   {
      ulong candidate=PositionGetTicket(i);
      if(candidate==0 || !PositionSelectByTicket(candidate))
         continue;
      ulong position_magic=(ulong)PositionGetInteger(POSITION_MAGIC);
      if(PositionGetString(POSITION_SYMBOL)==_Symbol
         && position_magic!=MagicNumber && position_magic!=peer_magic)
         return true;
   }
   return false;
}

string EffectiveDiagnosticFileName()
{
   return (SmallOnlyMode ? "Ramon_Small_Diagnostic.txt" : DiagnosticFileName);
}

string EffectiveSignalCsvFileName()
{
   return (SmallOnlyMode ? "Ramon_Small_Signals.csv" : SignalCsvFileName);
}

string EffectiveTradeCsvFileName()
{
   return (SmallOnlyMode ? "Ramon_Small_Trades.csv" : TradeCsvFileName);
}

string EffectiveShadowCsvFileName()
{
   if(!EnableImprovementShadowPack)
      return "";
   return (SmallOnlyMode ? "Ramon_Small_Shadow_Improvements.csv" : ShadowCsvFileName);
}

string BoolText(const bool value)
{
   return (value ? "YES" : "NO");
}

long BrokerUtcOffsetSeconds()
{
   long delta=(long)TimeCurrent()-(long)TimeGMT();
   long rounded=(long)(MathRound((double)delta/900.0)*900.0);
   if(MathAbs(rounded)>14*3600 || MathAbs(delta-rounded)>30)
      return 0;
   return rounded;
}

datetime BrokerTimeToUTC(const datetime broker_time)
{
   if(broker_time<=0)
      return 0;
   return (datetime)((long)broker_time-BrokerUtcOffsetSeconds());
}

string UTCText(const datetime broker_time,const int mode)
{
   datetime utc=BrokerTimeToUTC(broker_time);
   return (utc>0 ? TimeToString(utc,mode)+" UTC" : "NONE");
}

string AccountTypeText()
{
   return (AccountIsCent ? "CENT" : "STANDARD");
}

double AccountUnitsToUSD(const double units)
{
   if(MoneyUnitsPerUSD<=0.0)
      return 0.0;
   return units/MoneyUnitsPerUSD;
}

double SmallProfitRiskCapUnits()
{
   if(MoneyUnitsPerUSD<=0.0 || SmallProfitMaxRiskUSD<=0.0 || SmallProfitMaxLossUnits<=0.0)
      return 0.0;
   return MathMin(SmallProfitMaxLossUnits,SmallProfitMaxRiskUSD*MoneyUnitsPerUSD);
}

double EffectiveRiskPerTradeUSD()
{
   double multiplier=(LastRiskModelReady ? LastRiskMultiplier : 1.0);
   multiplier=MathMax(0.50,MathMin(1.50,multiplier));
   return RiskPerTradeUSD*multiplier;
}

bool MinimumLotExceedsRiskBudget()
{
   return (
      LastMinimumLotStopLossUnits>0.0
      && LastRiskBudgetUnits>0.0
      && LastMinimumLotStopLossUnits>LastRiskBudgetUnits+0.00001
   );
}

double MaxExecutableRiskUnits()
{
   return MaxExecutableRiskUSD*MoneyUnitsPerUSD;
}

bool MinimumLotOverrideEligible()
{
   return (
      AllowMinLotRiskOverride
      && MoneyUnitsPerUSD>0.0
      && MaxExecutableRiskUSD>=EffectiveRiskPerTradeUSD()
      && LastMinimumLotStopLossUnits>LastRiskBudgetUnits+0.00001
      && LastMinimumLotStopLossUnits<=MaxExecutableRiskUnits()+0.00001
   );
}

bool RiskGateBlocked()
{
   return MinimumLotExceedsRiskBudget() && !MinimumLotOverrideEligible();
}

string RiskGateText()
{
   if(!MinimumLotExceedsRiskBudget())
      return "PASS: preferred risk budget";
   if(MinimumLotOverrideEligible())
   {
      if(LastModelDecision=="WAIT")
         return "WOULD ALLOW MIN LOT: override <= $"+DoubleToString(MaxExecutableRiskUSD,2);
      return "PASS: MIN LOT OVERRIDE <= $"+DoubleToString(MaxExecutableRiskUSD,2);
   }
   if(LastModelDecision=="WAIT")
      return (LastMinimumLotStopLossUnits>MaxExecutableRiskUnits()+0.00001
         ? "WOULD BLOCK IF SIGNAL: min lot > hard cap"
         : "WOULD BLOCK IF SIGNAL: preferred budget exceeded; min lot override OFF");
   return (LastMinimumLotStopLossUnits>MaxExecutableRiskUnits()+0.00001
      ? "TRADE BLOCKED: min lot > hard cap"
      : "TRADE BLOCKED: preferred budget exceeded; min lot override OFF");
}

string BuildDiagnosticText()
{
   MqlTick tick;
   bool tick_ok=SymbolInfoTick(_Symbol,tick) && tick.bid>0.0 && tick.ask>tick.bid;
   int spread_points=(int)SymbolInfoInteger(_Symbol,SYMBOL_SPREAD);
   datetime closed=iTime(_Symbol,PERIOD_M15,1);
   int today=TradesToday();

   ulong ticket=0;
   datetime opened=0;
   bool managed=ManagedPosition(ticket,opened);
   string position_line="NONE";
   if(managed && PositionSelectByTicket(ticket))
   {
      long type=PositionGetInteger(POSITION_TYPE);
      string side=(type==POSITION_TYPE_BUY ? "BUY" : "SELL");
      position_line=side
         +" #"+IntegerToString((long)ticket)
         +" vol="+DoubleToString(PositionGetDouble(POSITION_VOLUME),2)
         +" open="+DoubleToString(PositionGetDouble(POSITION_PRICE_OPEN),_Digits)
         +" sl="+DoubleToString(PositionGetDouble(POSITION_SL),_Digits)
         +" tp="+DoubleToString(PositionGetDouble(POSITION_TP),_Digits)
         +" profit="+DoubleToString(PositionGetDouble(POSITION_PROFIT),2);
   }

   string text=
      "=== RAMON DIAGNOSTIC ===\n"
      +"EA version: 0.55.4\n"
      +"Range MAIN: "+BoolText(EnableRangeMain)+" | quick 5-unit TP, boundary SL, RR >=1.2, 30min maximum\n"
      +"AccountLossLimits: "+AccountLossLimitStatus+" | enabled="+BoolText(EnableAccountLossLimits)
      +" daily="+DoubleToString(DailyLossLimitPercent,2)+"% drawdown="+DoubleToString(MaximumEquityDrawdownPercent,2)+"%\n"
      +"NewsGuard: ACTIVE | entries -15/+15min; close -5min; calendar required\n"
      +"EA role: "+(SmallOnlyMode ? "SMALL 2c" : "PRIMARY")
      +"  Magic: "+IntegerToString((long)MagicNumber)+"\n"
      +"Captured: "+TimeToString(TimeGMT(),TIME_DATE|TIME_SECONDS)+" UTC\n"
      +"Symbol: "+_Symbol+"  Timeframe: M15\n"
      +"Bid: "+(tick_ok ? DoubleToString(tick.bid,_Digits) : "NA")
      +"  Ask: "+(tick_ok ? DoubleToString(tick.ask,_Digits) : "NA")
      +"  Spread(points): "+IntegerToString(spread_points)+"\n"
      +"Market: "+(SymbolInfoInteger(_Symbol,SYMBOL_TRADE_MODE)==SYMBOL_TRADE_MODE_FULL ? "OPEN/FULL" : "RESTRICTED")
      +"  TerminalConnected: "+BoolText((bool)TerminalInfoInteger(TERMINAL_CONNECTED))+"\n"+"Clock: UTC canonical  BrokerUTCOffsetSec: "+IntegerToString((int)BrokerUtcOffsetSeconds())+"\n\n"
      +"=== MODEL / SIGNAL ===\n"
      +"ModelUrl: "+ModelUrl+"\n"
      +"Snapshot cadence: "+IntegerToString(SnapshotIntervalSeconds)+"s"
      +"  Last request: "+(LastDecisionRequestTime>0
         ? UTCText(LastDecisionRequestTime,TIME_DATE|TIME_SECONDS) : "NONE")+"\n"
      +"Decision: "+LastModelDecision+"  Reason: "+LastModelReason+"\n"
      +"Attribution: "+AttributionSummary()+"\n"
      +"DecisionID: "+LastSampleKey+"  Saved: "+BoolText(LastSampleSaved)+"  Bundle: "+LastBundleId+"\n"
      +"TradeLearning*: "+TradeLearningStatus+"\n"
      +"LossCooldown: MT5_HISTORY | 2 consecutive same-direction net-loss SL closes | 30min\n"
      +"BaseDecision: "+LastBaseDecision+"  BaseReason: "+LastBaseReason+"\n"
      +"RoleModels: "+(LastRoleShadow ? "*SHADOW* (display only)" : (LastEnsembleReady ? "READY" : "*LEARNING*"))
      +"  Active: "+BoolText(LastEnsembleActive)
      +"  RegimeP: "+DoubleToString(LastRegimeProbability,3)
      +"  EntryP: "+DoubleToString(LastEntryProbability,3)
      +"  NewsP: "+DoubleToString(LastNewsProbability,3)
      +"  MetaP: "+DoubleToString(LastMetaProbability,3)
      +"  *ShadowRegime: "+LastShadowRegimeLabel
      +"  *ShadowRiskP: "+DoubleToString(LastShadowRiskProbability,3)
      +"  RiskReady: "+BoolText(LastRiskModelReady)
      +"  RiskP: "+DoubleToString(LastRiskProbability,3)
      +"  RiskMult: "+DoubleToString(LastRiskMultiplier,2)+"x\n"
      +"MOMENT LIVE: "+BoolText(LastMomentLiveActive)
      +" Fresh: "+BoolText(LastMomentLiveFresh)
      +" Veto: "+BoolText(LastMomentLiveVeto)
      +" Ratio: "+DoubleToString(LastMomentAnomalyRatio,3)
      +" Threshold: "+DoubleToString(LastMomentLiveThreshold,2)+"\n"
      +"FinBERT LIVE: "+BoolText(LastFinbertLiveActive)
      +" Veto: "+BoolText(LastFinbertLiveVeto)
      +" Sentiment: "+LastFinbertSentimentLabel
      +" Score: "+DoubleToString(LastFinbertDirectionalScore,3)
      +" Threshold: "+DoubleToString(LastFinbertLiveThreshold,2)+"\n"
      +"News: "+LastNewsSource
      +"  SourceReady: "+BoolText(LastNewsSourceReady)
      +"  ModelReady: "+BoolText(LastNewsModelReady)
      +"  AgeSec: "+DoubleToString(LastNewsSourceAgeSeconds,0)
      +"  Event: "+LastNewsEventCountry+" "+LastNewsEventImpact+" "+LastNewsEventTitle
      +"  DeltaMin: "+DoubleToString(LastNewsEventDeltaMinutes,1)+"\n"
      +"Signal bar: "+UTCText(LastSignalBarTime,TIME_DATE|TIME_MINUTES)
      +"  Last closed: "+UTCText(closed,TIME_DATE|TIME_MINUTES)+"\n"
      +"SignalBid: "+DoubleToString(LastSignalBid,_Digits)
      +"  SignalAsk: "+DoubleToString(LastSignalAsk,_Digits)+"\n"
      +"Forecast low/median/high: "
      +DoubleToString(LastForecastLow,_Digits)+" / "
      +DoubleToString(LastForecast,_Digits)+" / "
      +DoubleToString(LastForecastHigh,_Digits)+"\n"
      +"ATR: "+DoubleToString(LastAtr,2)
      +"  Edge: "+DoubleToString(LastEdge,_Digits)
      +"  ModelSpread(points): "+IntegerToString(LastModelSpreadPoints)+"\n"
      +"BuyEdge: "+DoubleToString(LastBuyEdge,_Digits)
      +"  SellEdge: "+DoubleToString(LastSellEdge,_Digits)
      +"  MinimumEdge: "+DoubleToString(LastMinimumEdge,_Digits)+"\n"
      +"Uncertainty: "+DoubleToString(LastUncertainty,_Digits)
      +"  SignalStrength: "+DoubleToString(LastSignalStrength,3)
      +"  MinimumStrength: "+DoubleToString(LastMinimumStrength,3)+"\n"
      +"EdgeCondition: "+((MathMax(LastBuyEdge,LastSellEdge)>=LastMinimumEdge && LastMinimumEdge>0.0) ? "PASS" : "FAIL")
      +"  StrengthCondition: "+((LastSignalStrength>=LastMinimumStrength && LastMinimumStrength>0.0) ? "PASS" : "FAIL")+"\n"
      +"IntrabarConfirm: "+(LastIntrabarConfirmed ? "PASS" : "FAIL")
      +"  Direction: "+LastIntrabarDirection
      +"  MoveATR: "+DoubleToString(LastIntrabarMoveAtr,3)+"/"+DoubleToString(LastIntrabarMinMoveAtr,3)
      +"  ReboundATR: "+DoubleToString(LastIntrabarReboundAtr,3)+"/"+DoubleToString(LastIntrabarMinReboundAtr,3)
      +"  StrengthFloor: "+DoubleToString(LastIntrabarMinStrength,3)+"\n"
      +"AITrendConfirm: "+(LastAiTrendConfirmed ? "PASS" : "FAIL")
      +"  Direction: "+LastAiTrendDirection
      +"  Score: "+DoubleToString(LastAiTrendScore,3)
      +"  MoveATR: "+DoubleToString(LastAiTrendMoveAtr,3)+"/"+DoubleToString(LastTrendMinPathAtr,3)
      +"  Consistency: "+DoubleToString(LastAiTrendConsistency,2)+"/"+DoubleToString(LastTrendMinConsistency,2)
      +"  EdgeFloor: "+DoubleToString(LastTrendMinEdgeFraction,2)+"x"
      +"  MicroFloor: "+DoubleToString(LastTrendMinMicroMoveAtr,3)+"\n"
      +"StopDistance: "+DoubleToString(LastStopDistance,_Digits)
      +"  TargetDistance: "+DoubleToString(LastTargetDistance,_Digits)+"\n"
      +"TargetLearning*: "+(LastTargetLearningActive ? "*COLLECTING*" : "OFF")
      +"  Structure: "+(LastTargetStructureReady ? "READY" : "FALLBACK")
      +"  Method: "+LastTargetMethod+"  Direction: "+LastTargetDirection+"\n"
      +"Impulse: "+DoubleToString(LastTargetImpulseStart,_Digits)
      +" -> "+DoubleToString(LastTargetImpulseEnd,_Digits)
      +"  RangeATR: "+DoubleToString(LastTargetImpulseAtr,3)+"\n"
      +"*TP1/TP2/TP3 learn: "+DoubleToString(LastTargetTP1,_Digits)
      +" / "+DoubleToString(LastTargetTP2,_Digits)
      +" / "+DoubleToString(LastTargetTP3,_Digits)
      +"  LegacyTP: "+DoubleToString(LastLegacyTargetPrice,_Digits)+"\n"
      +"ExecutionTargetMode: MAIN_TP3_BROKER_FAILSAFE_WHEN_VALID\n"      +"MainExitMode: TP1_TP2_TP3 + EARLY_ADVERSE + MAX_HOLD (60% risk, 2 weak snapshots)\n\n"
      +"=== * V0.50 IMPROVEMENT SHADOWS (OBSERVE ONLY) ===\n"
      +"*ShadowPack: "+BoolText(EnableImprovementShadowPack)
      +"  Reason: "+ShadowReason+"\n"
      +"*DirectionCaution: BUY="+BoolText(ShadowBuyCaution)
      +" SELL="+BoolText(ShadowSellCaution)+"\n"
      +"*ShadowRiskMultiplier: "+DoubleToString(ShadowRiskMultiplier,2)+"x"
      +"  ActualRiskMultiplier: "+DoubleToString(LastRiskMultiplier,2)+"x\n"
      +"*ShadowSmallTargetUnits: "+DoubleToString(ShadowSmallTargetUnits,2)
      +"  ActualTargetUnits: "+DoubleToString(SmallProfitTargetUnits,2)
      +"  StrongTargetCandidate: "+BoolText(ShadowSmallStrongTargetCandidate)+"\n"
      +"*ShadowSmallTPPlan: "+ShadowSmallTPPlan
      +"  Stage: "+IntegerToString(ShadowSmallTPStage)
      +"  Next: "+ShadowSmallTPNextAction+"\n"
      +"*DeadTradeShadow: "+BoolText(ShadowDeadTrade)
      +"  PeakR="+DoubleToString(ShadowDeadTradePeakR,3)
      +"  CurrentR="+DoubleToString(ShadowDeadTradeCurrentR,3)+"\n"
      +"*ShadowExecutionEffect: NONE\n"
      +"* = SHADOW / LEARNING / COLLECTING; not a live decision input unless explicitly activated.\n\n"
      +"=== ACCOUNT / EXECUTION ===\n"
      +"Live: "+LiveStateText()
      +"  AccountLock: "+(AccountLockHealthy() ? "OK" : "FAIL")
      +"  Server: "+AccountInfoString(ACCOUNT_SERVER)+"\n"
      +"AccountType: "+AccountTypeText()+" (configured)"
      +"  AccountCurrency: "+AccountInfoString(ACCOUNT_CURRENCY)
      +"  ExpectedCurrency: "+(StringLen(ExpectedAccountCurrency)>0 ? ExpectedAccountCurrency : "NOT_SET")+"\n"
      +"MoneyUnitsConfirmed: "+BoolText(ConfirmMoneyUnitsPerUSD)
      +"  MoneyUnitsPerUSD: "+DoubleToString(MoneyUnitsPerUSD,2)+"\n"
      +"BalanceUnits: "+DoubleToString(AccountInfoDouble(ACCOUNT_BALANCE),2)
      +"  BalanceUSDApprox: "+DoubleToString(AccountUnitsToUSD(AccountInfoDouble(ACCOUNT_BALANCE)),2)
      +"  EquityUSDApprox: "+DoubleToString(AccountUnitsToUSD(AccountInfoDouble(ACCOUNT_EQUITY)),2)+"\n"
      +"FreeMarginUnits: "+DoubleToString(AccountInfoDouble(ACCOUNT_MARGIN_FREE),2)
      +"  FreeMarginUSDApprox: "+DoubleToString(AccountUnitsToUSD(AccountInfoDouble(ACCOUNT_MARGIN_FREE)),2)+"\n"
      +"Trade permissions: terminal="+BoolText((bool)TerminalInfoInteger(TERMINAL_TRADE_ALLOWED))
      +" ea="+BoolText((bool)MQLInfoInteger(MQL_TRADE_ALLOWED))
      +" account="+BoolText((bool)AccountInfoInteger(ACCOUNT_TRADE_ALLOWED))+"\n"
      +"Status: "+StatusLine+"\n"
      +"Managed position: "+position_line+"\n"
      +"MarketClosedExitPause: "+BoolText(MarketClosedExitPause)
      +"  PauseTicket: "+IntegerToString((long)MarketClosedExitPauseTicket)
      +"  PauseTick: "+(MarketClosedExitPauseTickTime>0 ? UTCText(MarketClosedExitPauseTickTime,TIME_DATE|TIME_SECONDS) : "NONE")+"\n"
      +"ProfitProtection: "+(EnableProfitProtection ? "ACTIVE" : "OFF")
      +"  Armed: "+BoolText(ProfitProtectionArmed)
      +"  ShadowTrigger: "+BoolText(ProfitProtectionShadowTriggered)+"\n"
      +"ProfitProtectionUnits: current="+DoubleToString(ProfitProtectionCurrentUnits,2)
      +"  peak="+DoubleToString(ProfitProtectionPeakUnits,2)
      +"  giveback="+DoubleToString(ProfitProtectionGivebackNowUnits,2)
      +"  activate="+DoubleToString(ProfitProtectionActivationUnits,2)
      +"  triggerGiveback="+DoubleToString(ProfitProtectionGivebackUnits,2)
      +"  mode=DYNAMIC"
      +"  initialRisk="+DoubleToString(ProfitProtectionInitialRiskUnits,2)+"\n"
      +"TPStage: "+IntegerToString(TPStage)
      +"  status="+TPStageStatus
      +"  dir="+TPStageDirection
      +"  TP1/TP2/TP3="+DoubleToString(TPStageTP1,_Digits)+"/"
         +DoubleToString(TPStageTP2,_Digits)+"/"+DoubleToString(TPStageTP3,_Digits)
      +"  progress="+DoubleToString(TPStageProgress*100.0,1)+"%"
      +"  weak="+IntegerToString(TPStageWeakSnapshots)+"/"+IntegerToString(TPStageWeakSnapshotsRequired)+"\n"
      +"TPStageLock: "+TPStageLockStatus
      +"  LockedSL="+DoubleToString(TPStageLockedSL,_Digits)
      +"  OnTickCrossing=YES\n"
      +"EarlyProfitLock: "+(EnableEarlyProfitLock && !SmallOnlyMode ? "ACTIVE" : "OFF")
      +"  TriggerR="+DoubleToString(EarlyProfitLockActivation1R,2)
      +"/"+DoubleToString(EarlyProfitLockActivation2R,2)
      +"  LockR="+DoubleToString(EarlyProfitLockStage1R,2)
      +"/"+DoubleToString(EarlyProfitLockStage2R,2)+"\n"
      +"MainFastProfit: "+(EnableMainFastProfit && !SmallOnlyMode ? "ACTIVE" : "OFF")
      +"  status="+MainFastProfitStatus
      +"  ageBars>="+IntegerToString(MainFastProfitMinAgeBars)
      +"  progress="+DoubleToString(MainFastProfitProgress*100.0,1)+"%"
      +"  minProgress="+DoubleToString(MainFastProfitMinProgressToTP1*100.0,1)+"%"
      +"  weak="+IntegerToString(MainFastProfitWeakSnapshots)+"/"+IntegerToString(MainFastProfitWeakSnapshotsRequired)
      +"  minProfit="+DoubleToString(MainFastProfitMinProfitUnits,2)+"\n"
      +"EarlyReversalShadow: "+BoolText(EarlyReversalShadowTriggered)
      +"  observe="+BoolText(ObserveEarlyReversalExit)
      +"  minPeak="+DoubleToString(EarlyReversalMinPeakUnits,2)
      +"  giveback="+DoubleToString(EarlyReversalGivebackUnits,2)
      +"  maxCurrent="+DoubleToString(EarlyReversalMaxCurrentUnits,2)+"\n"
      +"EarlyAdverseExit: "+(EnableEarlyAdverseExit ? "ACTIVE" : "OFF")
      +"  triggered="+BoolText(EarlyAdverseTriggered)
      +"  lossTrigger="+DoubleToString(EarlyAdverseTriggerLossUnits,2)
      +"  weak="+IntegerToString(EarlyAdverseWeakSnapshots)+"/"+IntegerToString(EarlyAdverseWeakSnapshotsRequired)
      +"  riskFraction="+DoubleToString(
         (EarlyAdverseAppliedRiskFraction>0.0 ? EarlyAdverseAppliedRiskFraction
            : (SmallOnlyMode ? SmallEarlyAdverseRiskFraction : EarlyAdverseRiskFraction)),2)
      +"  minAgeSec="+IntegerToString(EarlyAdverseMinAgeSeconds)+"\n"
      +"Trades today: "+(today<0 ? "history unavailable" : IntegerToString(today))
      +"/"+(SmallOnlyMode ? "unlimited" : IntegerToString(MaxTradesPerDay))+"\n"
      +"Range MAIN: "+(EnableRangeMain && !SmallOnlyMode ? "ACTIVE" : "OFF")
      +"  TargetUnits: "+DoubleToString(RangeMainTargetUnits,2)
      +"  MaxLossUnits: "+DoubleToString(RangeMainMaxLossUnits,2)+"\n"
      +"SmallProfit: "+(SmallOnlyMode && EnableSmallProfitTrades ? "ACTIVE" : "OFF")
      +"  TargetUnits: "+DoubleToString(SmallProfitTargetUnits,2)
      +"  MaxRiskUSD: "+DoubleToString(SmallProfitMaxRiskUSD,2)
      +"  BrokerSLMaxUnits: "+DoubleToString(SmallProfitMaxLossUnits,2)
      +"  Today: "+(today<0 ? "history unavailable" : IntegerToString(today))
      +"/unlimited\n"
      +"Small entries per signal bar: "+IntegerToString(SmallProfitMaxEntriesPerSignalBar)
      +" max; second blocked after same-bar loss\n"      +"SmallEntryQualityFilter: ACTIVE"
      +"  Rule: edge>=minimum AND (intrabar OR ai_trend) same direction\n"
      +"RiskPerTradeUSD: "+DoubleToString(RiskPerTradeUSD,2)
      +"  EffectiveRiskUSD: "+DoubleToString(EffectiveRiskPerTradeUSD(),3)
      +"  RiskMultiplier: "+DoubleToString(LastRiskMultiplier,2)+"x"
      +"  RiskBudgetAccountUnits: "+DoubleToString(LastRiskBudgetUnits,2)
      +"  RiskBudgetUSD: "+DoubleToString(AccountUnitsToUSD(LastRiskBudgetUnits),4)+"\n"
      +"MinLotOverride: "+(AllowMinLotRiskOverride ? "ON" : "OFF")
      +"  MaxExecutableRiskUSD: "+DoubleToString(MaxExecutableRiskUSD,2)
      +"  OverrideUsed: "+BoolText(LastMinLotOverrideUsed)+"\n"
      +"SizingSide: "+LastSizingSide
      +"  PlannedVolume: "+DoubleToString(LastPlannedVolume,2)+"\n"
      +"EstimatedSLAccountUnits: "+DoubleToString(LastEstimatedStopLossUnits,2)
      +"  EstimatedSLUSD: "+DoubleToString(AccountUnitsToUSD(LastEstimatedStopLossUnits),4)+"\n"
      +"MinLotSLAccountUnits: "+DoubleToString(LastMinimumLotStopLossUnits,2)
      +"  MinExecutableRiskUSD: "+DoubleToString(AccountUnitsToUSD(LastMinimumLotStopLossUnits),4)+"\n"
      +"RiskGate: "+RiskGateText()+"\n"
      +"MaxSpreadPoints: "+IntegerToString(MaxSpreadPoints)
      +"  CSV: "+(WriteCsvLogs ? "ON" : "OFF")+"\n";

   return text;
}

void WriteDiagnostic()
{
   if(!WriteDiagnosticFile || StringLen(EffectiveDiagnosticFileName())==0)
      return;
   int handle=FileOpen(
      EffectiveDiagnosticFileName(),
      FILE_WRITE|FILE_TXT|FILE_ANSI|FILE_COMMON
   );
   if(handle==INVALID_HANDLE)
   {
      Print("Ramon diagnostic write failed err=",GetLastError());
      return;
   }
   FileWriteString(handle,BuildDiagnosticText());
   FileClose(handle);
}

void UiRect(const string name,const int x,const int y,const int w,const int h,const color bg,const color border)
{
   string object=UiPrefix+name;
   if(ObjectFind(0,object)<0)
      ObjectCreate(0,object,OBJ_RECTANGLE_LABEL,0,0,0);
   ObjectSetInteger(0,object,OBJPROP_CORNER,CORNER_LEFT_UPPER);
   ObjectSetInteger(0,object,OBJPROP_XDISTANCE,x);
   ObjectSetInteger(0,object,OBJPROP_YDISTANCE,y);
   ObjectSetInteger(0,object,OBJPROP_XSIZE,w);
   ObjectSetInteger(0,object,OBJPROP_YSIZE,h);
   ObjectSetInteger(0,object,OBJPROP_BGCOLOR,bg);
   ObjectSetInteger(0,object,OBJPROP_COLOR,border);
   ObjectSetInteger(0,object,OBJPROP_BACK,false);
   ObjectSetInteger(0,object,OBJPROP_SELECTABLE,false);
   ObjectSetInteger(0,object,OBJPROP_HIDDEN,true);
}

void UiLabel(const string name,const string text,const int x,const int y,const color text_color,const int size=9)
{
   string object=UiPrefix+name;
   if(ObjectFind(0,object)<0)
      ObjectCreate(0,object,OBJ_LABEL,0,0,0);
   ObjectSetInteger(0,object,OBJPROP_CORNER,CORNER_LEFT_UPPER);
   ObjectSetInteger(0,object,OBJPROP_XDISTANCE,x);
   ObjectSetInteger(0,object,OBJPROP_YDISTANCE,y);
   ObjectSetInteger(0,object,OBJPROP_COLOR,text_color);
   ObjectSetInteger(0,object,OBJPROP_FONTSIZE,size);
   ObjectSetInteger(0,object,OBJPROP_SELECTABLE,false);
   ObjectSetInteger(0,object,OBJPROP_HIDDEN,true);
   ObjectSetString(0,object,OBJPROP_FONT,"Arial");
   ObjectSetString(0,object,OBJPROP_TEXT,text);
}

void UiButton(const string name,const string text,const int x,const int y,const int w,const int h)
{
   string object=UiPrefix+name;
   if(ObjectFind(0,object)<0)
      ObjectCreate(0,object,OBJ_BUTTON,0,0,0);
   ObjectSetInteger(0,object,OBJPROP_CORNER,CORNER_LEFT_UPPER);
   ObjectSetInteger(0,object,OBJPROP_XDISTANCE,x);
   ObjectSetInteger(0,object,OBJPROP_YDISTANCE,y);
   ObjectSetInteger(0,object,OBJPROP_XSIZE,w);
   ObjectSetInteger(0,object,OBJPROP_YSIZE,h);
   ObjectSetInteger(0,object,OBJPROP_BGCOLOR,C'31,41,55');
   ObjectSetInteger(0,object,OBJPROP_COLOR,clrWhite);
   ObjectSetInteger(0,object,OBJPROP_BORDER_COLOR,C'75,85,99');
   ObjectSetInteger(0,object,OBJPROP_FONTSIZE,9);
   ObjectSetInteger(0,object,OBJPROP_SELECTABLE,false);
   ObjectSetInteger(0,object,OBJPROP_HIDDEN,true);
   ObjectSetString(0,object,OBJPROP_FONT,"Arial");
   ObjectSetString(0,object,OBJPROP_TEXT,text);
}

string PassFail(const bool value)
{
   return (value ? "PASS" : "FAIL");
}

color DirectionColor(const string direction)
{
   if(direction=="BUY")
      return clrLime;
   if(direction=="SELL")
      return clrDeepSkyBlue;
   return clrWhite;
}

string ForecastDirection()
{
   if(LastSignalBid<=0.0 || LastSignalAsk<=0.0 || LastForecast<=0.0)
      return "NONE";
   double mid=(LastSignalBid+LastSignalAsk)/2.0;
   if(LastForecast>mid) return "BUY";
   if(LastForecast<mid) return "SELL";
   return "NONE";
}

string EdgeDirection()
{
   if(LastBuyEdge>LastSellEdge) return "BUY";
   if(LastSellEdge>LastBuyEdge) return "SELL";
   return "NONE";
}

string ConfirmedDirection(const bool confirmed,const string direction)
{
   return (confirmed ? direction : "NONE");
}

int AttributionAgreementCount(const string decision)
{
   if(decision!="BUY" && decision!="SELL")
      return 0;
   int count=0;
   if(ForecastDirection()==decision) count++;
   if(EdgeDirection()==decision) count++;
   if(LastIntrabarConfirmed && LastIntrabarDirection==decision) count++;
   if(LastAiTrendConfirmed && LastAiTrendDirection==decision) count++;
   return count;
}

int AttributionConflictCount(const string decision)
{
   if(decision!="BUY" && decision!="SELL")
      return 0;
   string opposite=(decision=="BUY" ? "SELL" : "BUY");
   int count=0;
   if(ForecastDirection()==opposite) count++;
   if(EdgeDirection()==opposite) count++;
   if(LastIntrabarConfirmed && LastIntrabarDirection==opposite) count++;
   if(LastAiTrendConfirmed && LastAiTrendDirection==opposite) count++;
   return count;
}

int AttributionVoteCount(const string direction)
{
   int count=0;
   if(ForecastDirection()==direction) count++;
   if(EdgeDirection()==direction) count++;
   if(LastIntrabarConfirmed && LastIntrabarDirection==direction) count++;
   if(LastAiTrendConfirmed && LastAiTrendDirection==direction) count++;
   return count;
}

string AttributionSummary()
{
   return "VOTES F:"+ForecastDirection()
      +" E:"+EdgeDirection()
      +" I:"+ConfirmedDirection(LastIntrabarConfirmed,LastIntrabarDirection)
      +" T:"+ConfirmedDirection(LastAiTrendConfirmed,LastAiTrendDirection)
      +" | BUY "+IntegerToString(AttributionVoteCount("BUY"))
      +" SELL "+IntegerToString(AttributionVoteCount("SELL"));
}

void DeleteTPStageObjects()
{
   ObjectsDeleteAll(0,TpUiPrefix);
}

void DrawTPStageLevel(
   const string name,
   const double price,
   const color line_color,
   const ENUM_LINE_STYLE line_style,
   const int line_width,
   const bool reached
)
{
   string line=TpUiPrefix+name;
   if(price<=0.0)
   {
      ObjectDelete(0,line);
      return;
   }

   if(ObjectFind(0,line)<0)
   {
      if(!ObjectCreate(0,line,OBJ_HLINE,0,0,price))
         return;
   }

   ObjectSetDouble(0,line,OBJPROP_PRICE,price);
   ObjectSetInteger(0,line,OBJPROP_COLOR,line_color);
   ObjectSetInteger(0,line,OBJPROP_STYLE,line_style);
   ObjectSetInteger(0,line,OBJPROP_WIDTH,line_width);
   ObjectSetInteger(0,line,OBJPROP_BACK,false);
   ObjectSetInteger(0,line,OBJPROP_SELECTABLE,false);
   ObjectSetInteger(0,line,OBJPROP_SELECTED,false);
   ObjectSetInteger(0,line,OBJPROP_HIDDEN,false);
   ObjectSetString(0,line,OBJPROP_TOOLTIP,
      name+"  "+DoubleToString(price,_Digits)+(reached ? "  REACHED" : ""));
}

void UpdateTPStageObjects()
{
   if(!ShowTPLevelsOnChart || SmallOnlyMode)
   {
      DeleteTPStageObjects();
      return;
   }

   ulong ticket=0;
   datetime opened=0;
   if(!ManagedPosition(ticket,opened) || !LoadTPStagePlan(ticket))
   {
      DeleteTPStageObjects();
      return;
   }

   color tp1_color=(TPStage>=1 ? clrLime : clrDodgerBlue);
   color tp2_color=(TPStage>=2 ? clrLime : clrGold);
   color tp3_color=(TPStage>=3 ? clrLime : clrMagenta);

   DrawTPStageLevel("TP1",TPStageTP1,tp1_color,STYLE_DASH,1,TPStage>=1);
   DrawTPStageLevel("TP2",TPStageTP2,tp2_color,STYLE_DASH,1,TPStage>=2);
   DrawTPStageLevel("TP3",TPStageTP3,tp3_color,STYLE_SOLID,2,TPStage>=3);
}

string RoleProbabilityText(const double probability)
{
   return (probability<0.0 ? "N/A" : DoubleToString(probability*100.0,0)+"%");
}

string ModelTag(const string handler)
{
   if(StringFind(handler,"BinaryLogisticModel")>=0) return "Logistic";
   if(StringFind(handler,"chronos")>=0 || StringFind(handler,"Chronos")>=0) return "Chronos-2";
   if(StringFind(handler,"timesfm")>=0 || StringFind(handler,"TimesFM")>=0) return "TimesFM-3";
   if(StringFind(handler,"TargetStructure")>=0) return "TargetStructure";
   if(StringFind(handler,"MOMENT")>=0 || StringFind(handler,"moment")>=0) return "MOMENT-1-small";
   if(StringFind(handler,"finbert")>=0 || StringFind(handler,"FinBERT")>=0) return "FinBERT";
   if(handler=="OFF") return "OFF";
   if(handler=="") return "N/A";
   return handler;
}

void DrawDashboard()
{
   if(!ShowDashboard)
   {
      ObjectsDeleteAll(0,UiPrefix);
      return;
   }

   int spread_points=(int)SymbolInfoInteger(_Symbol,SYMBOL_SPREAD);
   int today=TradesToday();
   bool lock_ok=AccountLockHealthy();
   bool permissions=(bool)TerminalInfoInteger(TERMINAL_TRADE_ALLOWED)
      && (bool)MQLInfoInteger(MQL_TRADE_ALLOWED)
      && (bool)AccountInfoInteger(ACCOUNT_TRADE_ALLOWED);
   double dominant_edge=MathMax(LastBuyEdge,LastSellEdge);
   bool edge_pass=LastMinimumEdge>0.0 && dominant_edge>=LastMinimumEdge;
   bool strength_pass=LastMinimumStrength>0.0 && LastSignalStrength>=LastMinimumStrength;
   string dominant=(LastBuyEdge>=LastSellEdge ? "BUY" : "SELL");
   color state_color=DirectionColor(LastModelDecision);

   string live_reason="";
   bool live_ready=LiveExecutionReady(live_reason);
   color live_color=(live_ready && permissions && lock_ok ? clrWhite : clrTomato);
   color risk_gate_color=(RiskGateBlocked() ? clrTomato : clrWhite);
   color sizing_color=DirectionColor(LastSizingSide);

   // v0.53.2 display-only trade checklist helpers.
   bool checklist_spread_pass=(spread_points>0 && spread_points<=MaxSpreadPoints);
   bool checklist_volatility_ok=(LastAtr>0.0 && LastStopDistance>0.0 && LastTargetDistance>0.0);
   bool checklist_risk_exit_ok=(LastStopDistance>0.0 && LastTargetDistance>0.0);
   bool checklist_directional=(LastModelDecision=="BUY" || LastModelDecision=="SELL");
   bool checklist_entry_ready=checklist_directional
      && edge_pass && strength_pass
      && LastIntrabarConfirmed && LastAiTrendConfirmed
      && checklist_spread_pass && checklist_volatility_ok;

   string checklist_market_trend="UNCONFIRMED";
   color checklist_market_color=clrGold;
   if(LastAiTrendConfirmed && LastAiTrendDirection=="BUY")
   {
      checklist_market_trend="BULLISH";
      checklist_market_color=DirectionColor("BUY");
   }
   else if(LastAiTrendConfirmed && LastAiTrendDirection=="SELL")
   {
      checklist_market_trend="BEARISH";
      checklist_market_color=DirectionColor("SELL");
   }

   double checklist_rr=(LastStopDistance>0.0 ? LastTargetDistance/LastStopDistance : 0.0);

   ulong managed_ticket=0;
   datetime managed_opened=0;
   bool has_managed_position=ManagedPosition(managed_ticket,managed_opened);
   double live_profit_units=0.0;
   double live_profit_usd=0.0;
   if(has_managed_position && PositionSelectByTicket(managed_ticket))
   {
      live_profit_units=PositionGetDouble(POSITION_PROFIT);
      live_profit_usd=AccountUnitsToUSD(live_profit_units);
   }

   string pnl_units=(live_profit_units>0.00001 ? "+" : "")
      +DoubleToString(live_profit_units,2);
   string pnl_usd=(live_profit_usd>0.00001 ? "+$" :
      (live_profit_usd<-0.00001 ? "-$" : "$"))
      +DoubleToString(MathAbs(live_profit_usd),2);

   // Tall/narrow panel: summary text first, checklist directly underneath.
   UiRect("PANEL",12,24,560,1015,C'15,23,42',C'71,85,105');

   UiLabel("TITLE","RAMON AI TRADER  v0.55.4 "
      +(SmallOnlyMode ? "SMALL" : "MAIN"),28,36,clrWhite,12);
   UiLabel("SUB",_Symbol+" M15 | Forecast ["+ModelTag(LastForecastModelHandler)
      +"] | Shadow ["+ModelTag(LastForecastShadowModelHandler)+"] | "
      +IntegerToString(SnapshotIntervalSeconds)+"s",28,56,clrWhite,9);

   UiLabel("LIVE","LIVE: "+LiveStateText()
      +"   LOCK: "+(lock_ok ? "OK" : "FAIL")
      +"   PERMS: "+(permissions ? "OK" : "FAIL"),28,82,live_color,10);

   UiLabel("DECISION","DECISION: "+LastModelDecision+"   "+LastModelReason,
      28,108,state_color,11);
   UiLabel("ATTRIBUTION",AttributionSummary(),28,130,clrWhite,9);

   string compact_signal=(LastSignalBarTime>0 && LastSignalBid>0.0 && LastSignalAsk>LastSignalBid
      ? "Signal: "+UTCText(LastSignalBarTime,TIME_DATE|TIME_MINUTES)
         +" | Bid/Ask "+DoubleToString(LastSignalBid,_Digits)+"/"+DoubleToString(LastSignalAsk,_Digits)
      : "Signal: waiting for valid snapshot");
   UiLabel("SIGNAL",compact_signal,28,154,clrWhite,9);
   ObjectDelete(0,UiPrefix+"SIGNAL_PRICE");

   // The old Forecast / Edge / Strength / Intrabar / AI Trend rows were removed here
   // because the same live values now appear once in the checklist below.

   string role_mark=(LastRoleShadow || !LastEnsembleReady ? "*" : "");
   UiLabel("ROLE_MODELS",(LastRoleShadow ? "*SHADOW* | "+LastShadowRegimeLabel : "ROLE MODELS "+(LastEnsembleReady ? "READY" : "*LEARNING*"))
      +" R["+ModelTag(LastRegimeModelHandler)+"]"+role_mark+":"+RoleProbabilityText(LastRegimeProbability)
      +" E["+ModelTag(LastEntryModelHandler)+"]"+role_mark+":"+RoleProbabilityText(LastEntryProbability)
      +" N["+ModelTag(LastNewsModelHandler)+"]"+role_mark+":"+RoleProbabilityText(LastNewsProbability)
      +" M["+ModelTag(LastMetaModelHandler)+"]"+role_mark+":"+RoleProbabilityText(LastMetaProbability)
      +" SL["+ModelTag(LastRiskModelHandler)+"]"+role_mark+":"+RoleProbabilityText(LastRoleShadow ? LastShadowRiskProbability : LastRiskProbability),
      28,178,clrWhite,8);
   ObjectSetString(0,UiPrefix+"ROLE_MODELS",OBJPROP_TOOLTIP,
      "حالت سایه: فقط نمایش؛ بدون دخالت در معامله\n"
      "R: احتمال رونددار بودن، نه صعودی یا نزولی\n"
      "E: احتمال نتیجه مثبت ورود | N: برآورد مدل خبر\n"
      "M: برآورد ترکیبی | SL: احتمال برخورد به حد ضرر\n"
      "N/A: پیش‌بینی معتبر موجود نیست. درصدها دقت مدل نیستند.\n"
      "* = SHADOW / LEARNING / COLLECTING؛ هنوز Live نیست.");

   string moment_state=(LastMomentShadowReady
      ? LastMomentAnomalyLabel+" x"+DoubleToString(LastMomentAnomalyRatio,2)
      : "OFF");
   string finbert_state=(LastFinbertShadowReady
      ? LastFinbertSentimentLabel+" "+DoubleToString(LastFinbertDirectionalScore,2)
      : "OFF");
   string moment_gate=(LastMomentLiveActive
      ? (LastMomentLiveVeto ? "VETO" : (LastMomentLiveFresh ? "PASS" : "WARMING"))
      : "OFF");
   string finbert_gate=(LastFinbertLiveActive
      ? (LastFinbertLiveVeto ? "VETO" : "PASS")
      : "OFF");
   color ai_gate_color=(LastMomentLiveVeto || LastFinbertLiveVeto ? clrTomato : clrWhite);
   UiLabel("LIVE_AI","ANOMALY ["+ModelTag(LastAnomalyModelHandler)+"] "+moment_state
      +" {LIVE "+moment_gate+"}"
      +" | SENTIMENT ["+ModelTag(LastNewsSentimentModelHandler)+"] "+finbert_state
      +" {LIVE "+finbert_gate+"}",
      28,200,ai_gate_color,8);
   ObjectSetString(0,UiPrefix+"LIVE_AI",OBJPROP_TOOLTIP,
      "LIVE GATE: MOMENT و FinBERT می‌توانند ورود BUY/SELL را به WAIT تبدیل کنند؛ خودشان معامله جدید ایجاد نمی‌کنند.");

   string news_title=(StringLen(LastNewsEventTitle)>28
      ? StringSubstr(LastNewsEventTitle,0,28)+"..." : LastNewsEventTitle);
   string news_delta=(LastNewsEventTime>0
      ? (LastNewsEventDeltaMinutes>=0.0 ? " in " : " ")
         +DoubleToString(MathAbs(LastNewsEventDeltaMinutes),0)+"m"
      : "");
   color news_color=(!LastNewsSourceReady ? clrTomato : clrWhite);

   UiLabel("NEWS","NEWS ["+ModelTag(LastNewsModelHandler)+"] via "+LastNewsSourceHandler
      +" | "+LastNewsSource+" "+(LastNewsSourceReady ? "READY" : "OFFLINE")
      +" | "+(LastNewsModelReady ? "MODEL READY" : "*LEARNING*")
      +" | "+LastNewsEventImpact+" "+LastNewsEventCountry+" "+news_title+news_delta,
      28,222,news_color,8);

   UiLabel("ACCOUNT","Account: "+AccountTypeText()
      +" | "+AccountInfoString(ACCOUNT_CURRENCY)
      +" | Balance "+DoubleToString(AccountInfoDouble(ACCOUNT_BALANCE),2)+"u"
      +" (~$"+DoubleToString(AccountUnitsToUSD(AccountInfoDouble(ACCOUNT_BALANCE)),2)+")"
      +" | Trades "+(today<0 ? "?" : IntegerToString(today))
      +"/"+(SmallOnlyMode ? "unlimited" : IntegerToString(MaxTradesPerDay)),
      28,244,clrWhite,9);
   ObjectDelete(0,UiPrefix+"BALANCE_USD");

   UiLabel("LIVE_PNL",
      has_managed_position
         ? "LIVE P/L: "+pnl_units+" units   ~= "+pnl_usd
         : "LIVE P/L: --   (no Ramon position)",
      28,266,clrWhite,10);

   UiLabel("SIZING","Sizing: "+LastSizingSide
      +"   Vol: "+DoubleToString(LastPlannedVolume,2)
      +"   Base: $"+DoubleToString(RiskPerTradeUSD,2)
      +" x"+DoubleToString(LastRiskMultiplier,2)
      +" = $"+DoubleToString(EffectiveRiskPerTradeUSD(),3)
      +"   Cap: $"+DoubleToString(MaxExecutableRiskUSD,2),
      28,288,sizing_color,9);

   // Detailed min-risk/gate/money-unit values remain in Trade Check + Diagnostic.
   ObjectDelete(0,UiPrefix+"MIN_RISK");
   ObjectDelete(0,UiPrefix+"RISK_GATE");
   ObjectDelete(0,UiPrefix+"MONEY_CONFIRM");

   // ---------------------- checklist ----------------------
   int tx=24, ty=320, tw=520, th=366, row_h=28;
   UiRect("CHECK_TABLE_BG",tx,ty,tw,th,C'17,27,46',C'71,85,105');
   UiRect("CHECK_TABLE_HEAD",tx+4,ty+4,tw-8,28,C'30,41,59',C'71,85,105');

   UiLabel("CHECK_TITLE","10-POINT TRADE CHECK",tx+12,ty+10,clrWhite,10);
   UiLabel("CHECK_SUMMARY",
      "MKT "+checklist_market_trend+" | MODEL "+LastModelDecision
      +" | ENTRY "+(checklist_entry_ready ? "READY" : "NOT READY"),
      tx+188,ty+10,(checklist_entry_ready ? state_color : clrGold),8);

   UiLabel("CHECK_H1","#",tx+12,ty+42,clrWhite,8);
   UiLabel("CHECK_H2","CHECK",tx+38,ty+42,clrWhite,8);
   UiLabel("CHECK_H3","LIVE VALUE",tx+184,ty+42,clrWhite,8);
   UiLabel("CHECK_H4","STATUS",tx+442,ty+42,clrWhite,8);

   int ry=ty+62;
   for(int r=0;r<10;r++)
      UiRect("CHECK_ROW_BG_"+IntegerToString(r+1),tx+4,ry+r*row_h,tw-8,row_h-2,
         (r%2==0 ? C'20,31,52' : C'15,24,42'),C'38,50,68');

   string cvals[10];
   cvals[0]=LastModelDecision+" | "+LastModelReason+" | "+ModelTag(LastForecastModelHandler);
   cvals[1]=checklist_market_trend+" | AI "+LastAiTrendDirection+" | "+ModelTag(LastMarketStateHandler);
   cvals[2]=DoubleToString(LastSignalStrength,3)+" / "+DoubleToString(LastMinimumStrength,3);
   cvals[3]=dominant+" "+DoubleToString(dominant_edge,2)+" / "+DoubleToString(LastMinimumEdge,2);
   cvals[4]=LastIntrabarDirection+" | move "+DoubleToString(LastIntrabarMoveAtr,3)
      +" | rb "+DoubleToString(LastIntrabarReboundAtr,3);
   cvals[5]=LastAiTrendDirection+" | score "+DoubleToString(LastAiTrendScore,3)
      +" | path "+DoubleToString(LastAiTrendMoveAtr,3)
      +" | cons "+DoubleToString(LastAiTrendConsistency,2);
   cvals[6]=(LastTargetStructureReady
      ? LastTargetMethod+" | "+LastTargetDirection
      : "target structure learning")+" | "+ModelTag(LastTargetModelHandler);
   cvals[7]="ATR "+DoubleToString(LastAtr,2)
      +" | SL "+DoubleToString(LastStopDistance,2)
      +" | TP "+DoubleToString(LastTargetDistance,2);
   cvals[8]="spread "+IntegerToString(spread_points)+" / max "+IntegerToString(MaxSpreadPoints);
   cvals[9]="SL "+DoubleToString(LastStopDistance,2)
      +" | TP "+DoubleToString(LastTargetDistance,2)
      +" | RR "+DoubleToString(checklist_rr,2)
      +" | "+ModelTag(LastRiskModelHandler);

   string cnames[10]={"Direction","Market trend","Trend strength","Model edge",
      "Intrabar timing","AI trend","Market structure","Volatility","Entry cost","Risk / exit"};
   string cstats[10];
   color ccolors[10];

   cstats[0]=(checklist_directional ? "SIGNAL" : "WAIT");
   cstats[1]=(LastAiTrendConfirmed ? "CONFIRMED" : "NOT READY");
   cstats[2]=PassFail(strength_pass);
   cstats[3]=PassFail(edge_pass);
   cstats[4]=PassFail(LastIntrabarConfirmed);
   cstats[5]=PassFail(LastAiTrendConfirmed);
   cstats[6]=(LastTargetStructureReady ? "READY" : "*LEARNING*");
   cstats[7]=(checklist_volatility_ok ? "DATA OK" : "INVALID");
   cstats[8]=PassFail(checklist_spread_pass);
   cstats[9]=(checklist_risk_exit_ok ? "DEFINED" : "INVALID");

   for(int i=0;i<10;i++) ccolors[i]=clrWhite;
   ccolors[0]=(checklist_directional ? state_color : clrWhite);
   ccolors[1]=(LastAiTrendConfirmed ? checklist_market_color : clrGold);
   ccolors[2]=(strength_pass ? clrWhite : clrGold);
   ccolors[3]=(edge_pass ? DirectionColor(dominant) : clrGold);
   ccolors[4]=(LastIntrabarConfirmed ? DirectionColor(LastIntrabarDirection) : clrGold);
   ccolors[5]=(LastAiTrendConfirmed ? DirectionColor(LastAiTrendDirection) : clrGold);
   ccolors[6]=(LastTargetStructureReady ? clrWhite : clrGold);
   ccolors[7]=(checklist_volatility_ok ? clrWhite : clrTomato);
   ccolors[8]=(checklist_spread_pass ? clrWhite : clrGold);
   ccolors[9]=(checklist_risk_exit_ok ? clrWhite : clrTomato);

   for(int i=0;i<10;i++)
   {
      int cy=ry+7+i*row_h;
      UiLabel("CXN_"+IntegerToString(i),IntegerToString(i+1),tx+12,cy,clrWhite,8);
      UiLabel("CXK_"+IntegerToString(i),cnames[i],tx+38,cy,clrWhite,8);
      UiLabel("CXV_"+IntegerToString(i),cvals[i],tx+184,cy,clrWhite,8);
      UiLabel("CXS_"+IntegerToString(i),cstats[i],tx+442,cy,ccolors[i],8);
   }

   UiLabel("CHECK_NOTE","TRADE CHECK | * = SHADOW / LEARNING",
      tx+12,ty+346,clrWhite,8);

   // ---------------------- model / handler map ----------------------
   int mx=24, my=700, mw=520, mh=244;
   UiRect("MODEL_MAP_BG",mx,my,mw,mh,C'17,27,46',C'71,85,105');
   UiRect("MODEL_MAP_HEAD",mx+4,my+4,mw-8,26,C'30,41,59',C'71,85,105');
   UiLabel("MODEL_MAP_TITLE","MODEL / HANDLER MAP",mx+12,my+9,clrWhite,10);

   string mnames[12]={"Forecast","Forecast Shadow","Regime","Anomaly Detection",
      "Entry","News Calendar","News Model","News Sentiment",
      "Meta","Risk / SL","Market State","TP Structure"};
   string mhandlers[12]={
      ModelTag(LastForecastModelHandler),
      ModelTag(LastForecastShadowModelHandler),
      ModelTag(LastRegimeModelHandler),
      ModelTag(LastAnomalyModelHandler)+" [LIVE GATE]",
      ModelTag(LastEntryModelHandler),
      LastNewsSourceHandler,
      ModelTag(LastNewsModelHandler),
      ModelTag(LastNewsSentimentModelHandler)+" [LIVE GATE]",
      ModelTag(LastMetaModelHandler),
      ModelTag(LastRiskModelHandler),
      ModelTag(LastMarketStateHandler),
      ModelTag(LastTargetModelHandler)
   };

   string mtips[12];
   mtips[0]="Forecast | "+ModelTag(LastForecastModelHandler)+"\n"
      +"Decision: "+LastModelDecision+" | Reason: "+LastModelReason+"\n"
      +"Low/Median/High: "+DoubleToString(LastForecastLow,_Digits)+" / "
      +DoubleToString(LastForecast,_Digits)+" / "+DoubleToString(LastForecastHigh,_Digits)+"\n"
      +"BUY edge: "+DoubleToString(LastBuyEdge,3)+" | SELL edge: "+DoubleToString(LastSellEdge,3)+"\n"
      +"Strength: "+DoubleToString(LastSignalStrength,3)+" / min "+DoubleToString(LastMinimumStrength,3);

   mtips[1]="Forecast Shadow | "+ModelTag(LastForecastShadowModelHandler)+"\n"
      +"Ready: "+BoolText(LastTimesFMReady)+" | Direction: "+LastTimesFMDirection+"\n"
      +"Low/Median/High: "+DoubleToString(LastTimesFMLow,_Digits)+" / "
      +DoubleToString(LastTimesFMMedian,_Digits)+" / "+DoubleToString(LastTimesFMHigh,_Digits)+"\n"
      +"Move ATR: "+DoubleToString(LastTimesFMMoveAtr,3)
      +" | Agrees Chronos: "+BoolText(LastTimesFMAgreesChronos)+"\n"
      +"SHADOW: does not create or veto trades.";

   mtips[2]="Regime | "+ModelTag(LastRegimeModelHandler)+"\n"
      +"Probability: "+RoleProbabilityText(LastRegimeProbability)+"\n"
      +"Label: "+LastShadowRegimeLabel+"\n"
      +"Current market state: "+LastMarketState+" | route "+LastMarketStateRoute+"\n"
      +"Role active: "+BoolText(LastEnsembleActive);

   mtips[3]="Anomaly Detection | "+ModelTag(LastAnomalyModelHandler)+"\n"
      +"Label: "+LastMomentAnomalyLabel+"\n"
      +"Score: "+DoubleToString(LastMomentAnomalyScore,6)+" | Ratio: "+DoubleToString(LastMomentAnomalyRatio,3)+"\n"
      +"Threshold: "+DoubleToString(LastMomentLiveThreshold,2)+" | Fresh: "+BoolText(LastMomentLiveFresh)+"\n"
      +"LIVE veto: "+BoolText(LastMomentLiveVeto);

   mtips[4]="Entry | "+ModelTag(LastEntryModelHandler)+"\n"
      +"Entry probability: "+RoleProbabilityText(LastEntryProbability)+"\n"
      +"Signal strength: "+DoubleToString(LastSignalStrength,3)+" / "+DoubleToString(LastMinimumStrength,3)+"\n"
      +"Edge BUY/SELL: "+DoubleToString(LastBuyEdge,3)+" / "+DoubleToString(LastSellEdge,3)+"\n"
      +"Intrabar: "+LastIntrabarDirection+" "+BoolText(LastIntrabarConfirmed)
      +" | AI trend: "+LastAiTrendDirection+" "+BoolText(LastAiTrendConfirmed)+"\n"
      +"Role active: "+BoolText(LastEnsembleActive);

   mtips[5]="News Calendar | "+LastNewsSourceHandler+"\n"
      +"Source: "+LastNewsSource+" | Ready: "+BoolText(LastNewsSourceReady)+"\n"
      +"Event: "+LastNewsEventCountry+" "+LastNewsEventImpact+" "+LastNewsEventTitle+"\n"
      +"Delta: "+DoubleToString(LastNewsEventDeltaMinutes,1)+" min | Age: "
      +DoubleToString(LastNewsSourceAgeSeconds,0)+" sec";

   mtips[6]="News Model | "+ModelTag(LastNewsModelHandler)+"\n"
      +"Probability: "+RoleProbabilityText(LastNewsProbability)+"\n"
      +"Model ready: "+BoolText(LastNewsModelReady)+"\n"
      +"Event input: "+LastNewsEventCountry+" "+LastNewsEventImpact+" "+LastNewsEventTitle+"\n"
      +"Role active: "+BoolText(LastEnsembleActive);

   mtips[7]="News Sentiment | "+ModelTag(LastNewsSentimentModelHandler)+"\n"
      +"Sentiment: "+LastFinbertSentimentLabel+"\n"
      +"Directional score: "+DoubleToString(LastFinbertDirectionalScore,3)+"\n"
      +"Threshold: "+DoubleToString(LastFinbertLiveThreshold,2)+"\n"
      +"LIVE veto: "+BoolText(LastFinbertLiveVeto);

   mtips[8]="Meta | "+ModelTag(LastMetaModelHandler)+"\n"
      +"Meta probability: "+RoleProbabilityText(LastMetaProbability)+"\n"
      +"Base decision: "+LastBaseDecision+" | "+LastBaseReason+"\n"
      +"Final decision: "+LastModelDecision+" | "+LastModelReason+"\n"
      +"Ensemble ready/active: "+BoolText(LastEnsembleReady)+" / "+BoolText(LastEnsembleActive);

   mtips[9]="Risk / SL | "+ModelTag(LastRiskModelHandler)+"\n"
      +"Risk probability: "+RoleProbabilityText(LastRiskProbability)+" | Target: "+LastRiskTarget+"\n"
      +"Risk multiplier: "+DoubleToString(LastRiskMultiplier,2)+"x\n"
      +"SL distance: "+DoubleToString(LastStopDistance,3)+" | TP distance: "+DoubleToString(LastTargetDistance,3)+"\n"
      +"Planned volume: "+DoubleToString(LastPlannedVolume,2)+" | Side: "+LastSizingSide;

   mtips[10]="Market State | "+ModelTag(LastMarketStateHandler)+"\n"
      +"State: "+LastMarketState+"\n"
      +"Route: "+LastMarketStateRoute+"\n"
      +"Policy: "+LastMarketStatePolicy+"\n"
      +"ATR: "+DoubleToString(LastAtr,3)+" | Spread: "+IntegerToString(LastModelSpreadPoints)+" pts";

   mtips[11]="TP Structure | "+ModelTag(LastTargetModelHandler)+"\n"
      +"Ready: "+BoolText(LastTargetStructureReady)+" | Method: "+LastTargetMethod+"\n"
      +"Direction: "+LastTargetDirection+" | Impulse ATR: "+DoubleToString(LastTargetImpulseAtr,3)+"\n"
      +"TP1/TP2/TP3: "+DoubleToString(LastTargetTP1,_Digits)+" / "
      +DoubleToString(LastTargetTP2,_Digits)+" / "+DoubleToString(LastTargetTP3,_Digits);

   for(int mi=0;mi<12;mi++)
   {
      int mrow=my+36+mi*16;
      string name_id="MODEL_NAME_"+IntegerToString(mi);
      string handler_id="MODEL_HANDLER_"+IntegerToString(mi);
      UiLabel(name_id,mnames[mi],mx+12,mrow,clrWhite,8);
      UiLabel(handler_id,"-> "+mhandlers[mi],mx+170,mrow,clrWhite,8);
      ObjectSetString(0,UiPrefix+name_id,OBJPROP_TOOLTIP,mtips[mi]);
      ObjectSetString(0,UiPrefix+handler_id,OBJPROP_TOOLTIP,mtips[mi]);
   }

   UiButton("COPY","COPY DIAGNOSTIC",28,958,176,30);
   UiButton("CLOSE","CLOSE TRADE",218,958,110,30);
   ObjectSetInteger(0,UiPrefix+"CLOSE",OBJPROP_BGCOLOR,
      has_managed_position ? C'153,27,27' : C'55,65,81');
   ObjectSetInteger(0,UiPrefix+"CLOSE",OBJPROP_BORDER_COLOR,
      has_managed_position ? C'248,113,113' : C'75,85,99');

   UiLabel("COPY_STATUS",LastCopyStatus,340,958,
      (StringFind(LastCopyStatus,"failed")>=0 || StringFind(LastCopyStatus,"disabled")>=0
         ? clrTomato : clrWhite),8);
   UiLabel("CLOSE_STATUS",LastCloseStatus,340,974,
      (StringFind(LastCloseStatus,"FAILED")>=0 ? clrTomato : clrWhite),8);

   ChartRedraw();
}

bool CopyDiagnosticToClipboard()
{
   WriteDiagnostic();
   if(!EnableClipboardButton)
   {
      LastCopyStatus="Clipboard button disabled";
      return false;
   }
   if(!(bool)MQLInfoInteger(MQL_DLLS_ALLOWED))
   {
      LastCopyStatus="Enable DLL imports to copy";
      return false;
   }

   string text=BuildDiagnosticText();
   ulong bytes=(ulong)(StringLen(text)+1)*2;
   long hmem=GlobalAlloc(RAMON_GMEM_MOVEABLE,bytes);
   if(hmem==0)
   {
      LastCopyStatus="Clipboard alloc failed";
      return false;
   }

   long ptr=GlobalLock(hmem);
   if(ptr==0)
   {
      GlobalFree(hmem);
      LastCopyStatus="Clipboard lock failed";
      return false;
   }
   lstrcpyW(ptr,text);
   GlobalUnlock(hmem);

   long hwnd=ChartGetInteger(0,CHART_WINDOW_HANDLE);
   if(OpenClipboard(hwnd)==0)
   {
      GlobalFree(hmem);
      LastCopyStatus="Clipboard open failed";
      return false;
   }
   if(EmptyClipboard()==0)
   {
      CloseClipboard();
      GlobalFree(hmem);
      LastCopyStatus="Clipboard clear failed";
      return false;
   }
   if(SetClipboardData(RAMON_CF_UNICODETEXT,hmem)==0)
   {
      CloseClipboard();
      GlobalFree(hmem);
      LastCopyStatus="Clipboard set failed";
      return false;
   }
   CloseClipboard();
   LastCopyStatus="COPIED "+TimeToString(TimeGMT(),TIME_SECONDS)+" UTC";
   return true;
}

datetime NewsHighEventUTC=0;
datetime NewsGuardReceivedUTC=0;
bool NewsGuardWindow(const int before_minutes)
{
   if(NewsHighEventUTC<=0) return false;
   long delta=(long)NewsHighEventUTC-(long)TimeGMT();
   return delta<=before_minutes*60 && delta>=-15*60;
}
bool NewsGuardEntryBlocked()
{
   return !LastNewsSourceReady || NewsGuardReceivedUTC<=0
      || LastNewsSourceAgeSeconds+(TimeGMT()-NewsGuardReceivedUTC)>1800
      || NewsGuardWindow(15);
}
bool ManageNewsGuard(const ulong ticket)
{
   if(!NewsGuardWindow(5)) return false;
   StatusLine="NEWS GUARD: close before high-impact USD news";
   if(ManagedExitPausedForMarketClosed(ticket)) return true;
   if(Trade.PositionClose(ticket,MaxDeviationPoints))
   {
      ResetMarketClosedExitPause();
      RecordDealTelemetry(Trade.ResultDeal(),"news_guard_exit");
      StatusLine="NEWS GUARD: position closed";
   }
   else HandleManagedExitFailure(ticket,"NEWS GUARD EXIT");
   return true;
}

void ShowStatus()
{
   // Clear the closed position's runtime before any diagnostic or chart render.
   ulong status_ticket=0;
   datetime status_opened=0;
   if(!ManagedPosition(status_ticket,status_opened))
   {
      ResetProfitProtectionState();
      ResetTPStageRuntime();
      ResetEarlyAdverseState();
      ResetMainFastProfitState();
      ResetMarketClosedExitPause();
   }
   WriteDiagnostic();
   UpdateTPStageObjects();
   DrawDashboard();
}
bool JsonText(const string json,const string key,string &value)
{
   string marker="\""+key+"\":\"";
   int start=StringFind(json,marker);
   if(start<0) return false;
   start+=StringLen(marker);
   int finish=StringFind(json,"\"",start);
   if(finish<0) return false;
   value=StringSubstr(json,start,finish-start);
   return true;
}

bool JsonNumber(const string json,const string key,double &value)
{
   string marker="\""+key+"\":";
   int start=StringFind(json,marker);
   if(start<0) return false;
   start+=StringLen(marker);
   int finish=start;
   while(finish<StringLen(json))
   {
      ushort c=StringGetCharacter(json,finish);
      if((c>=48 && c<=57) || c==45 || c==46 || c==43 || c==69 || c==101)
         finish++;
      else
         break;
   }
   if(finish<=start) return false;
   value=StringToDouble(StringSubstr(json,start,finish-start));
   return MathIsValidNumber(value);
}

bool BuildRequest(string &payload,datetime &bar_time)
{
   MqlRates bars[];
   ArraySetAsSeries(bars,true);
   int copied=CopyRates(_Symbol,PERIOD_M15,1,256,bars);
   if(copied<128) { StatusLine="Need 128 completed bars"; return false; }
   bar_time=bars[0].time;
   MqlTick tick;
   if(!SymbolInfoTick(_Symbol,tick) || tick.bid<=0.0 || tick.ask<=tick.bid)
   { StatusLine="Bad tick"; return false; }
   if(TimeCurrent()-tick.time>30)
   { StatusLine="Stale tick"; return false; }
   datetime current=iTime(_Symbol,PERIOD_M15,0);
   if(current<=bar_time || current-bar_time>1800)
   { StatusLine="Stale completed bar"; return false; }

   payload="{\"symbol\":\""+_Symbol+"\",\"timeframe\":\"M15\",\"bid\":"
      +DoubleToString(tick.bid,_Digits)+",\"ask\":"
      +DoubleToString(tick.ask,_Digits)+",\"point\":"
      +DoubleToString(SymbolInfoDouble(_Symbol,SYMBOL_POINT),_Digits)+",\"bars\":[";
   for(int i=copied-1;i>=0;i--)
   {
      if(i<copied-1) payload+=",";
      payload+="{\"time\":"+IntegerToString((long)bars[i].time)
         +",\"open\":"+DoubleToString(bars[i].open,_Digits)
         +",\"high\":"+DoubleToString(bars[i].high,_Digits)
         +",\"low\":"+DoubleToString(bars[i].low,_Digits)
         +",\"close\":"+DoubleToString(bars[i].close,_Digits)+"}";
   }
   payload+="],\"micro_bars\":[";
   MqlRates micro[];
   ArraySetAsSeries(micro,true);
   int micro_copied=CopyRates(_Symbol,PERIOD_M1,0,4,micro);
   if(micro_copied>=3)
   {
      for(int j=micro_copied-1;j>=0;j--)
      {
         if(j<micro_copied-1) payload+=",";
         payload+="{\"time\":"+IntegerToString((long)micro[j].time)
            +",\"open\":"+DoubleToString(micro[j].open,_Digits)
            +",\"high\":"+DoubleToString(micro[j].high,_Digits)
            +",\"low\":"+DoubleToString(micro[j].low,_Digits)
            +",\"close\":"+DoubleToString(micro[j].close,_Digits)+"}";
      }
   }
   payload+="],\"quote_time\":"+IntegerToString((long)tick.time)
      +",\"range_execution_ready\":"+((EnableRangeMain && !SmallOnlyMode) ? "true" : "false")+"}";
   return true;
}

bool QueryModel(const string payload,string &reply)
{
   char request[],response[];
   StringToCharArray(payload,request,0,WHOLE_ARRAY,CP_UTF8);
   ArrayResize(request,ArraySize(request)-1); // Remove terminal NUL from JSON body.
   string headers="Content-Type: application/json\r\n";
   string response_headers="";
   ResetLastError();
   int code=WebRequest("POST",ModelUrl,headers,RequestTimeoutMs,request,response,response_headers);
   if(code!=200)
   {
      StatusLine="Model HTTP "+IntegerToString(code)+" err "+IntegerToString(GetLastError());
      return false;
   }
   reply=CharArrayToString(response,0,ArraySize(response),CP_UTF8);
   return true;
}

int TradesToday()
{
   datetime now=TimeCurrent();
   datetime start=StringToTime(TimeToString(now,TIME_DATE));
   if(!HistorySelect(start,now)) return -1;
   int count=0;
   for(int i=0;i<HistoryDealsTotal();i++)
   {
      ulong deal=HistoryDealGetTicket(i);
      if(deal==0) continue;
      if(HistoryDealGetString(deal,DEAL_SYMBOL)!=_Symbol) continue;
      if((ulong)HistoryDealGetInteger(deal,DEAL_MAGIC)!=MagicNumber) continue;
      if(HistoryDealGetInteger(deal,DEAL_ENTRY)==DEAL_ENTRY_IN) count++;
   }
   return count;
}

int SmallEntriesThisSignalBar(const datetime bar_time)
{
   datetime current=iTime(_Symbol,PERIOD_M15,0);
   datetime now=TimeCurrent();
   if(current<=bar_time || current-bar_time>1800 || now<current
      || !HistorySelect(current,now))
      return -1;
   int count=0;
   for(int i=0;i<HistoryDealsTotal();i++)
   {
      ulong deal=HistoryDealGetTicket(i);
      if(deal==0 || HistoryDealGetString(deal,DEAL_SYMBOL)!=_Symbol
         || (ulong)HistoryDealGetInteger(deal,DEAL_MAGIC)!=MagicNumber
         || HistoryDealGetInteger(deal,DEAL_ENTRY)!=DEAL_ENTRY_IN)
         continue;
      count++;
   }
   if(LastEntrySignalBar==bar_time)
      if(LastSmallEntriesOnSignalBar>count)
         count=LastSmallEntriesOnSignalBar;
   return count;
}

// Result: -1 history unreadable, 0 no closed SMALL loss in this execution bar, 1 loss found.
// This blocks a second attempt after the first SMALL trade on the same M15 idea has already failed.
int SmallLossClosedThisSignalBar(const datetime bar_time)
{
   if(!SmallOnlyMode)
      return 0;

   datetime current=iTime(_Symbol,PERIOD_M15,0);
   datetime now=TimeCurrent();
   if(current<=bar_time || current-bar_time>1800 || now<current
      || !HistorySelect(current,now))
      return -1;

   ulong identifiers[];
   for(int i=0;i<HistoryDealsTotal();i++)
   {
      ulong deal=HistoryDealGetTicket(i);
      if(deal==0)
         return -1;
      if(HistoryDealGetString(deal,DEAL_SYMBOL)!=_Symbol
         || (ulong)HistoryDealGetInteger(deal,DEAL_MAGIC)!=MagicNumber
         || HistoryDealGetInteger(deal,DEAL_ENTRY)!=DEAL_ENTRY_IN)
         continue;

      ulong identifier=(ulong)HistoryDealGetInteger(deal,DEAL_POSITION_ID);
      if(identifier==0)
         return -1;

      bool seen=false;
      for(int j=0;j<ArraySize(identifiers);j++)
         if(identifiers[j]==identifier) { seen=true; break; }
      if(!seen)
      {
         int n=ArraySize(identifiers);
         ArrayResize(identifiers,n+1);
         identifiers[n]=identifier;
      }
   }

   for(int i=0;i<ArraySize(identifiers);i++)
   {
      ulong identifier=identifiers[i];
      if(PositionIdOpen(identifier))
         continue;
      if(!HistorySelectByPosition(identifier))
         return -1;

      bool ours=false;
      double in_volume=0.0,out_volume=0.0,net=0.0;
      for(int j=0;j<HistoryDealsTotal();j++)
      {
         ulong deal=HistoryDealGetTicket(j);
         if(deal==0)
            return -1;
         if(HistoryDealGetString(deal,DEAL_SYMBOL)!=_Symbol)
            continue;

         net+=HistoryDealGetDouble(deal,DEAL_PROFIT)
            +HistoryDealGetDouble(deal,DEAL_COMMISSION)
            +HistoryDealGetDouble(deal,DEAL_SWAP)
            +HistoryDealGetDouble(deal,DEAL_FEE);

         long type=HistoryDealGetInteger(deal,DEAL_TYPE);
         if(type!=DEAL_TYPE_BUY && type!=DEAL_TYPE_SELL)
            continue;

         long entry=HistoryDealGetInteger(deal,DEAL_ENTRY);
         double volume=HistoryDealGetDouble(deal,DEAL_VOLUME);
         if(entry==DEAL_ENTRY_IN)
         {
            if((ulong)HistoryDealGetInteger(deal,DEAL_MAGIC)!=MagicNumber)
               return -1;
            ours=true;
            in_volume+=volume;
         }
         else if(entry==DEAL_ENTRY_OUT || entry==DEAL_ENTRY_OUT_BY)
            out_volume+=volume;
      }

      if(ours && in_volume>0.0
         && MathAbs(in_volume-out_volume)<=0.000001
         && net<0.0)
         return 1;
   }
   return 0;
}

bool SmallProfitCandidate(const string decision,const string reason,
   const double buy_edge,const double sell_edge,const double strength,
   string &direction,string &filter_reason)
{
   direction="";
   filter_reason="NOT_CANDIDATE";
   if(!SmallOnlyMode || !EnableSmallProfitTrades || decision!="WAIT"
      || (reason!="insufficient_model_edge" && reason!="insufficient_model_strength")
      || strength<=0.0)
      return false;

   if(buy_edge>sell_edge && buy_edge>0.0) direction="BUY";
   else if(sell_edge>buy_edge && sell_edge>0.0) direction="SELL";
   if(direction=="")
   {
      filter_reason="SMALL_FILTER_NO_DIRECTION";
      return false;
   }

   double directional_edge=(direction=="BUY" ? buy_edge : sell_edge);
   if(LastMinimumEdge<=0.0 || directional_edge<LastMinimumEdge)
   {
      filter_reason="SMALL_FILTER_EDGE_FAIL";
      return false;
   }

   bool intrabar_support=(
      LastIntrabarConfirmed
      && LastIntrabarDirection==direction
   );
   bool trend_support=(
      LastAiTrendConfirmed
      && LastAiTrendDirection==direction
   );
   if(!intrabar_support && !trend_support)
   {
      filter_reason="SMALL_FILTER_CONFIRM_FAIL";
      return false;
   }

   filter_reason=(intrabar_support && trend_support
      ? "SMALL_FILTER_PASS_BOTH"
      : (intrabar_support ? "SMALL_FILTER_PASS_INTRABAR" : "SMALL_FILTER_PASS_TREND"));
   return true;
}

bool SmallProfitTarget(const ENUM_ORDER_TYPE side,const double entry,
   const double volume,const MqlTick &quote,double &target)
{
   double point=SymbolInfoDouble(_Symbol,SYMBOL_POINT);
   double tick_size=SymbolInfoDouble(_Symbol,SYMBOL_TRADE_TICK_SIZE);
   if(tick_size<=0.0) tick_size=point;
   if(tick_size<=0.0 || volume<=0.0) return false;
   double sign=(side==ORDER_TYPE_BUY ? 1.0 : -1.0);
   double unit_gain=0.0;
   if(!OrderCalcProfit(side,_Symbol,volume,entry,entry+sign,unit_gain)
      || unit_gain<=0.0)
      return false;
   double min_stop=(double)SymbolInfoInteger(_Symbol,SYMBOL_TRADE_STOPS_LEVEL)*point;
   double required_from_quote=(side==ORDER_TYPE_BUY ? quote.bid : quote.ask);
   double distance=MathMax(SmallProfitTargetUnits/unit_gain,
      MathAbs(required_from_quote-entry)+min_stop+2*point);
   double steps=MathCeil(distance/tick_size-0.00000001);
   for(int attempt=0;attempt<32;attempt++)
   {
      target=NormalizeDouble(entry+sign*(steps+attempt)*tick_size,_Digits);
      double gain=0.0;
      if(!OrderCalcProfit(side,_Symbol,volume,entry,target,gain)) return false;
      if(gain+0.00001>=SmallProfitTargetUnits)
         return gain<=SmallProfitTargetUnits+0.25;
   }
   return false;
}

bool RangeMainRewardRiskValid(const ENUM_ORDER_TYPE side,const double entry,
   const double volume,const double stop,const double target)
{
   double loss=0.0,gain=0.0;
   return OrderCalcProfit(side,_Symbol,volume,entry,stop,loss)
      && OrderCalcProfit(side,_Symbol,volume,entry,target,gain)
      && loss<0.0 && gain>0.0 && gain+0.00001>=1.2*(-loss);
}

bool RangeMainProfitTarget(const ENUM_ORDER_TYPE side,const double entry,
   const double volume,const MqlTick &quote,const double midpoint,double &target)
{
   double point=SymbolInfoDouble(_Symbol,SYMBOL_POINT);
   double tick_size=SymbolInfoDouble(_Symbol,SYMBOL_TRADE_TICK_SIZE);
   if(tick_size<=0.0) tick_size=point;
   if(tick_size<=0.0 || volume<=0.0 || RangeMainTargetUnits<=0.0) return false;
   double sign=(side==ORDER_TYPE_BUY ? 1.0 : -1.0);
   double unit_gain=0.0;
   if(!OrderCalcProfit(side,_Symbol,volume,entry,entry+sign,unit_gain)
      || unit_gain<=0.0)
      return false;
   double min_stop=(double)SymbolInfoInteger(_Symbol,SYMBOL_TRADE_STOPS_LEVEL)*point;
   double required_from_quote=(side==ORDER_TYPE_BUY ? quote.bid : quote.ask);
   double distance=MathMax(RangeMainTargetUnits/unit_gain,
      MathAbs(required_from_quote-entry)+min_stop+2*point);
   double steps=MathCeil(distance/tick_size-0.00000001);
   for(int attempt=0;attempt<32;attempt++)
   {
      double candidate=NormalizeDouble(entry+sign*(steps+attempt)*tick_size,_Digits);
      // A quick RANGE target must be reached before the range midpoint.
      if(midpoint>0.0
         && ((side==ORDER_TYPE_BUY && candidate>midpoint)
            || (side==ORDER_TYPE_SELL && candidate<midpoint)))
         return false;
      double gain=0.0;
      if(!OrderCalcProfit(side,_Symbol,volume,entry,candidate,gain)) return false;
      if(gain+0.00001>=RangeMainTargetUnits)
      {
         target=candidate;
         return gain<=RangeMainTargetUnits+0.25;
      }
   }
   return false;
}

bool SmallProfitStop(const ENUM_ORDER_TYPE side,const double entry,
   const double model_stop,const double volume,const MqlTick &quote,double &stop)
{
   double point=SymbolInfoDouble(_Symbol,SYMBOL_POINT);
   double tick_size=SymbolInfoDouble(_Symbol,SYMBOL_TRADE_TICK_SIZE);
   if(tick_size<=0.0) tick_size=point;
   if(tick_size<=0.0 || volume<=0.0) return false;
   double sign=(side==ORDER_TYPE_BUY ? -1.0 : 1.0);
   double unit_loss=0.0;
   if(!OrderCalcProfit(side,_Symbol,volume,entry,entry+sign,unit_loss)
      || unit_loss>=0.0)
      return false;
   double risk_cap=SmallProfitRiskCapUnits();
   if(risk_cap<=0.0) return false;
   double max_distance=risk_cap/(-unit_loss);
   double model_distance=MathAbs(model_stop-entry);
   int steps=(int)MathMax(1.0,MathFloor(MathMin(max_distance,model_distance)/tick_size+0.00000001)-1.0);
   if(steps<1) return false;
   double min_stop=(double)SymbolInfoInteger(_Symbol,SYMBOL_TRADE_STOPS_LEVEL)*point;
   double required_from_quote=(side==ORDER_TYPE_BUY ? quote.bid : quote.ask);
   for(int attempt=0;attempt<64 && steps+attempt<=max_distance/tick_size+0.00000001;attempt++)
   {
      double candidate=NormalizeDouble(entry+sign*(steps+attempt)*tick_size,_Digits);
      if(side==ORDER_TYPE_BUY && candidate>required_from_quote-min_stop-2*point)
         continue;
      if(side==ORDER_TYPE_SELL && candidate<required_from_quote+min_stop+2*point)
         continue;
      double loss=0.0;
      if(!OrderCalcProfit(side,_Symbol,volume,entry,candidate,loss)) return false;
      if(loss<0.0 && -loss<=risk_cap+0.00001)
      { stop=candidate; return true; }
   }
   return false;
}

double SelectVolume(ENUM_ORDER_TYPE direction,double entry,double stop)
{
   double minimum=SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_MIN);
   double maximum=SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_MAX);
   double step=SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_STEP);
   if(minimum<=0.0 || step<=0.0 || maximum<minimum || MoneyUnitsPerUSD<=0.0)
      return 0.0;
   double money=0.0;
   if(!OrderCalcProfit(direction,_Symbol,minimum,entry,stop,money) || money>=0.0)
      return 0.0;
   double budget=EffectiveRiskPerTradeUSD()*MoneyUnitsPerUSD;
   double hard_cap=MaxExecutableRiskUSD*MoneyUnitsPerUSD;
   if(budget<=0.0 || hard_cap<=0.0)
      return 0.0;
   if(-money>budget+0.00001)
   {
      if(!AllowMinLotRiskOverride || MaxExecutableRiskUSD<EffectiveRiskPerTradeUSD()
         || -money>hard_cap+0.00001)
         return 0.0;
      // Override is deliberately minimum-lot only; never scale volume using the larger cap.
      return minimum;
   }
   double steps=MathFloor((budget/(-money)*minimum-minimum)/step+0.00000001);
   double volume=MathMin(maximum,minimum+steps*step);
   volume=NormalizeDouble(volume,8);
   if(!OrderCalcProfit(direction,_Symbol,volume,entry,stop,money))
      return 0.0;
   while(-money>budget+0.00001 && volume>minimum)
   {
      volume=NormalizeDouble(volume-step,8);
      if(!OrderCalcProfit(direction,_Symbol,volume,entry,stop,money))
         return 0.0;
   }
   return (-money<=budget+0.00001 ? volume : 0.0);
}

void ClearPendingSizing()
{
   PendingSizingSampleKey="";
   PendingSizingRiskBudgetUnits=0.0;
   PendingSizingPlannedVolume=0.0;
   PendingSizingMinLotSLUnits=0.0;
   PendingSizingOverrideUsed=false;
   PendingSizingMaxExecutableRiskUSD=0.0;
   PendingSizingMoneyUnitsPerUSD=0.0;
}

bool StageEntrySizing(
   const string sample_key,
   const ENUM_ORDER_TYPE side,
   const double entry,
   const double stop,
   const double volume
)
{
   ClearPendingSizing();
   double minimum=SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_MIN);
   double min_loss=0.0;
   if(!ValidSampleKey(sample_key) || minimum<=0.0 || volume<=0.0
      || !OrderCalcProfit(side,_Symbol,minimum,entry,stop,min_loss) || min_loss>=0.0)
      return false;
   double budget=(SmallOnlyMode ? SmallProfitRiskCapUnits()
      : EffectiveRiskPerTradeUSD()*MoneyUnitsPerUSD);
   double hard_cap=(SmallOnlyMode ? SmallProfitRiskCapUnits()
      : MaxExecutableRiskUSD*MoneyUnitsPerUSD);
   if(budget<=0.0 || hard_cap<=0.0 || MoneyUnitsPerUSD<=0.0)
      return false;
   double min_risk=MathAbs(min_loss);
   PendingSizingSampleKey=sample_key;
   PendingSizingRiskBudgetUnits=budget;
   PendingSizingPlannedVolume=volume;
   PendingSizingMinLotSLUnits=min_risk;
   PendingSizingOverrideUsed=(
      !SmallOnlyMode
      && AllowMinLotRiskOverride
      && min_risk>budget+0.00001
      && volume<=minimum+0.00000001
      && min_risk<=hard_cap+0.00001
   );
   PendingSizingMaxExecutableRiskUSD=(SmallOnlyMode ? SmallProfitMaxRiskUSD : MaxExecutableRiskUSD);
   PendingSizingMoneyUnitsPerUSD=MoneyUnitsPerUSD;
   return true;
}

void UpdateSizingPreview()
{
   LastRiskBudgetUnits=(SmallOnlyMode ? SmallProfitRiskCapUnits()
      : EffectiveRiskPerTradeUSD()*MoneyUnitsPerUSD);
   LastSizingSide=(LastModelDecision=="BUY" || LastModelDecision=="SELL"
      ? LastModelDecision
      : (LastBuyEdge>=LastSellEdge ? "BUY" : "SELL"));
   LastPlannedVolume=0.0;
   LastEstimatedStopLossUnits=0.0;
   LastMinimumLotStopLossUnits=0.0;
   LastMinLotOverrideUsed=false;

   if(LastSignalBid<=0.0 || LastSignalAsk<=LastSignalBid || LastStopDistance<=0.0)
      return;

   ENUM_ORDER_TYPE side=(LastSizingSide=="BUY" ? ORDER_TYPE_BUY : ORDER_TYPE_SELL);
   double entry=(side==ORDER_TYPE_BUY ? LastSignalAsk : LastSignalBid);
   double point=SymbolInfoDouble(_Symbol,SYMBOL_POINT);
   double min_stop=(double)SymbolInfoInteger(_Symbol,SYMBOL_TRADE_STOPS_LEVEL)*point;
   double distance=MathMax(LastStopDistance,min_stop+2*point);
   double stop=NormalizeDouble(entry+(side==ORDER_TYPE_BUY ? -distance : distance),_Digits);
   double minimum=SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_MIN);
   double money=0.0;

   if(SmallOnlyMode)
   {
      MqlTick quote;
      if(minimum<=0.0 || !SymbolInfoTick(_Symbol,quote)) return;
      entry=(side==ORDER_TYPE_BUY ? quote.ask : quote.bid);
      stop=NormalizeDouble(entry+(side==ORDER_TYPE_BUY ? -distance : distance),_Digits);
      if(!SmallProfitStop(side,entry,stop,minimum,quote,stop)
         || !OrderCalcProfit(side,_Symbol,minimum,entry,stop,money) || money>=0.0)
         return;
      LastMinimumLotStopLossUnits=-money;
      LastEstimatedStopLossUnits=-money;
      LastPlannedVolume=minimum;
      LastMinLotOverrideUsed=(-money>LastRiskBudgetUnits+0.00001);
      return;
   }

   if(minimum>0.0 && OrderCalcProfit(side,_Symbol,minimum,entry,stop,money) && money<0.0)
      LastMinimumLotStopLossUnits=-money;

   double volume=SelectVolume(side,entry,stop);
   LastPlannedVolume=volume;
   if(volume>0.0 && OrderCalcProfit(side,_Symbol,volume,entry,stop,money) && money<0.0)
   {
      LastEstimatedStopLossUnits=-money;
      LastMinLotOverrideUsed=(
         MinimumLotExceedsRiskBudget()
         && volume<=minimum+0.00000001
         && MinimumLotOverrideEligible()
      );
   }
}

void UpdateImprovementShadows()
{
   ShadowBuyCaution=false;
   ShadowSellCaution=false;
   ShadowRiskMultiplier=1.0;
   ShadowSmallTargetUnits=SmallProfitTargetUnits;
   ShadowSmallStrongTargetCandidate=false;
   ShadowSmallTPStage=0;
   ShadowSmallTPNextAction="NONE";
   ShadowDeadTrade=false;
   ShadowDeadTradePeakR=0.0;
   ShadowDeadTradeCurrentR=0.0;
   ShadowReason="NONE";

   if(!EnableImprovementShadowPack)
      return;

   string direction=(LastModelDecision=="BUY" || LastModelDecision=="SELL"
      ? LastModelDecision
      : (LastBuyEdge>=LastSellEdge ? "BUY" : "SELL"));
   bool intrabar_support=(LastIntrabarConfirmed && LastIntrabarDirection==direction);
   bool trend_support=(LastAiTrendConfirmed && LastAiTrendDirection==direction);
   bool edge_support=(LastMinimumEdge>0.0
      && (direction=="BUY" ? LastBuyEdge : LastSellEdge)>=LastMinimumEdge);
   bool strength_support=(LastMinimumStrength>0.0
      && LastSignalStrength>=LastMinimumStrength);

   int support_count=0;
   if(intrabar_support) support_count++;
   if(trend_support) support_count++;
   if(edge_support) support_count++;
   if(strength_support) support_count++;

   // Direction-specific shadow: BUY has historically underperformed SELL in the current report.
   // Observe only whether requiring at least one short-horizon confirmation would have filtered it.
   ShadowBuyCaution=(direction=="BUY" && !intrabar_support && !trend_support);
   ShadowSellCaution=(direction=="SELL" && !intrabar_support && !trend_support);

   // Proposed future sizing policy, telemetry only.
   if(support_count<=1) ShadowRiskMultiplier=0.50;
   else if(support_count==2) ShadowRiskMultiplier=0.75;
   else ShadowRiskMultiplier=1.00;

   // Proposed SMALL target extension, telemetry only. Actual broker TP remains unchanged.
   // Require all three short-horizon confirmations so the normal 2-cent target
   // remains untouched for ordinary SMALL entries.
   ShadowSmallStrongTargetCandidate=(
      SmallOnlyMode
      && intrabar_support
      && trend_support
      && edge_support
   );
   if(ShadowSmallStrongTargetCandidate)
      ShadowSmallTargetUnits=ShadowSmallStrongTargetUnits;

   ulong ticket=0;
   datetime opened=0;
   if(ManagedPosition(ticket,opened) && PositionSelectByTicket(ticket))
   {
      double current=PositionGetDouble(POSITION_PROFIT);
      if(SmallOnlyMode)
      {
         if(current>=ShadowSmallTP3Units)
         {
            ShadowSmallTPStage=3;
            ShadowSmallTPNextAction="WOULD_CLOSE_AT_TP3";
         }
         else if(current>=ShadowSmallTP2Units)
         {
            ShadowSmallTPStage=2;
            ShadowSmallTPNextAction=(intrabar_support && trend_support && edge_support
               ? "WOULD_HOLD_FOR_TP3" : "WOULD_CLOSE_AT_TP2");
         }
         else if(current>=ShadowSmallTP1Units)
         {
            ShadowSmallTPStage=1;
            ShadowSmallTPNextAction=(intrabar_support && trend_support && edge_support
               ? "WOULD_HOLD_FOR_TP2" : "WOULD_CLOSE_AT_TP1");
         }
         else
            ShadowSmallTPNextAction="WAITING_FOR_TP1";
      }

      double risk=ManagedPositionInitialRiskUnits(ticket);
      double peak=ProfitProtectionPeakUnits;
      if(risk>0.0)
      {
         ShadowDeadTradePeakR=peak/risk;
         ShadowDeadTradeCurrentR=current/risk;
         int age_sec=(opened>0 ? (int)(TimeCurrent()-opened) : 0);

         // "Dead-trade" candidate: after two minutes, little favorable excursion,
         // already materially adverse, and no fresh directional confirmation.
         ShadowDeadTrade=(
            age_sec>=120
            && ShadowDeadTradePeakR<0.25
            && ShadowDeadTradeCurrentR<=-0.35
            && !intrabar_support
            && !trend_support
         );
      }
   }

   if(ShadowDeadTrade) ShadowReason="dead_trade_candidate";
   else if(ShadowBuyCaution) ShadowReason="buy_confirmation_caution";
   else if(ShadowSellCaution) ShadowReason="sell_confirmation_caution";
   else if(ShadowRiskMultiplier<1.0) ShadowReason="reduced_risk_candidate";
   else if(ShadowSmallTargetUnits>SmallProfitTargetUnits) ShadowReason="small_tp_extension_candidate";
   else ShadowReason="no_shadow_action";
}

void AppendImprovementShadowCsv()
{
   if(!WriteCsvLogs || !EnableImprovementShadowPack
      || StringLen(EffectiveShadowCsvFileName())==0)
      return;

   int handle=FileOpen(
      EffectiveShadowCsvFileName(),
      FILE_READ|FILE_WRITE|FILE_CSV|FILE_ANSI|FILE_COMMON,
      ','
   );
   if(handle==INVALID_HANDLE)
   {
      Print("Ramon shadow CSV open failed err=",GetLastError());
      return;
   }

   bool empty=(FileSize(handle)==0);
   FileSeek(handle,0,SEEK_END);
   if(empty)
   {
      FileWrite(handle,
         "captured","signal_bar_time","symbol","role","sample_key",
         "decision","reason","buy_caution","sell_caution",
         "shadow_risk_multiplier","actual_risk_multiplier",
         "shadow_small_target_units","actual_small_target_units",
         "shadow_small_strong_target_candidate","shadow_small_tp_stage",
         "shadow_small_tp_next_action",
         "dead_trade_candidate","dead_trade_peak_r","dead_trade_current_r",
         "intrabar_confirmed","intrabar_direction",
         "ai_trend_confirmed","ai_trend_direction",
         "buy_edge","sell_edge","minimum_edge",
         "signal_strength","minimum_strength","shadow_reason");
   }

   FileWrite(handle,
      TimeToString(TimeCurrent(),TIME_DATE|TIME_SECONDS),
      TimeToString(LastSignalBarTime,TIME_DATE|TIME_MINUTES),
      _Symbol,(SmallOnlyMode ? "SMALL" : "MAIN"),LastSampleKey,
      LastModelDecision,LastModelReason,
      BoolText(ShadowBuyCaution),BoolText(ShadowSellCaution),
      DoubleToString(ShadowRiskMultiplier,2),DoubleToString(LastRiskMultiplier,2),
      DoubleToString(ShadowSmallTargetUnits,2),DoubleToString(SmallProfitTargetUnits,2),
      BoolText(ShadowSmallStrongTargetCandidate),IntegerToString(ShadowSmallTPStage),
      ShadowSmallTPNextAction,
      BoolText(ShadowDeadTrade),DoubleToString(ShadowDeadTradePeakR,4),
      DoubleToString(ShadowDeadTradeCurrentR,4),
      BoolText(LastIntrabarConfirmed),LastIntrabarDirection,
      BoolText(LastAiTrendConfirmed),LastAiTrendDirection,
      DoubleToString(LastBuyEdge,6),DoubleToString(LastSellEdge,6),
      DoubleToString(LastMinimumEdge,6),DoubleToString(LastSignalStrength,6),
      DoubleToString(LastMinimumStrength,6),ShadowReason);

   FileFlush(handle);
   FileClose(handle);
}

string CsvField(const string value)
{
   string escaped=value;
   StringReplace(escaped,"\"","\"\"");
   return "\""+escaped+"\"";
}

void AppendSignalCsv()
{
   if(!WriteCsvLogs || StringLen(EffectiveSignalCsvFileName())==0)
      return;
   int handle=FileOpen(
      EffectiveSignalCsvFileName(),
      FILE_READ|FILE_WRITE|FILE_ANSI|FILE_COMMON,
      ','
   );
   if(handle==INVALID_HANDLE)
   {
      Print("Ramon signal CSV open failed err=",GetLastError());
      return;
   }

   bool empty=(FileSize(handle)==0);
   FileSeek(handle,0,SEEK_END);

   if(empty)
   {
      string header=
         "captured,signal_bar_time,symbol,decision,reason,"
         "forecast_direction,edge_direction,intrabar_vote,trend_vote,"
         "attribution_agree,attribution_conflict,attribution_buy_votes,attribution_sell_votes,"
         "base_decision,base_reason,ensemble_ready,ensemble_active,"
         "regime_probability,entry_probability,meta_probability,risk_multiplier,"
         "signal_bid,signal_ask,spread_points,"
         "forecast_low,forecast_median,forecast_high,atr,"
         "buy_edge,sell_edge,minimum_edge,uncertainty,"
         "signal_strength,minimum_strength,"
         "intrabar_confirmed,intrabar_direction,intrabar_move_atr,intrabar_rebound_atr,"
         "intrabar_min_strength,intrabar_min_move_atr,intrabar_min_rebound_atr,"
         "ai_trend_confirmed,ai_trend_direction,ai_trend_score,"
         "ai_trend_move_atr,ai_trend_consistency,"
         "trend_min_path_atr,trend_min_consistency,trend_min_edge_fraction,trend_min_micro_move_atr,"
         "stop_distance,target_distance,"
         "live_armed,account_lock,account_type,account_currency,"
         "balance_units,balance_usd_approx,"
         "risk_usd,money_units_per_usd,risk_budget_units,"
         "allow_min_lot_override,max_executable_risk_usd,min_lot_override_used,"
         "sizing_side,planned_volume,estimated_sl_units,estimated_sl_usd,"
         "min_lot_sl_units,min_executable_risk_usd,min_lot_blocked\r\n";
      FileWriteString(handle,header);
   }

   string row="";
   row+=CsvField(TimeToString(TimeCurrent(),TIME_DATE|TIME_SECONDS))+",";
   row+=CsvField(TimeToString(LastSignalBarTime,TIME_DATE|TIME_MINUTES))+",";
   row+=CsvField(_Symbol)+",";
   row+=CsvField(LastModelDecision)+",";
   row+=CsvField(LastModelReason)+",";
   row+=CsvField(ForecastDirection())+",";
   row+=CsvField(EdgeDirection())+",";
   row+=CsvField(ConfirmedDirection(LastIntrabarConfirmed,LastIntrabarDirection))+",";
   row+=CsvField(ConfirmedDirection(LastAiTrendConfirmed,LastAiTrendDirection))+",";
   row+=IntegerToString(AttributionAgreementCount(LastModelDecision))+",";
   row+=IntegerToString(AttributionConflictCount(LastModelDecision))+",";
   row+=IntegerToString(AttributionVoteCount("BUY"))+",";
   row+=IntegerToString(AttributionVoteCount("SELL"))+",";
   row+=CsvField(LastBaseDecision)+",";
   row+=CsvField(LastBaseReason)+",";
   row+=CsvField(BoolText(LastEnsembleReady))+",";
   row+=CsvField(BoolText(LastEnsembleActive))+",";
   row+=DoubleToString(LastRegimeProbability,6)+",";
   row+=DoubleToString(LastEntryProbability,6)+",";
   row+=DoubleToString(LastMetaProbability,6)+",";
   row+=DoubleToString(LastRiskMultiplier,6)+",";
   row+=DoubleToString(LastSignalBid,_Digits)+",";
   row+=DoubleToString(LastSignalAsk,_Digits)+",";
   row+=IntegerToString(LastModelSpreadPoints)+",";
   row+=DoubleToString(LastForecastLow,_Digits)+",";
   row+=DoubleToString(LastForecast,_Digits)+",";
   row+=DoubleToString(LastForecastHigh,_Digits)+",";
   row+=DoubleToString(LastAtr,4)+",";
   row+=DoubleToString(LastBuyEdge,_Digits)+",";
   row+=DoubleToString(LastSellEdge,_Digits)+",";
   row+=DoubleToString(LastMinimumEdge,_Digits)+",";
   row+=DoubleToString(LastUncertainty,_Digits)+",";
   row+=DoubleToString(LastSignalStrength,6)+",";
   row+=DoubleToString(LastMinimumStrength,6)+",";
   row+=CsvField(BoolText(LastIntrabarConfirmed))+",";
   row+=CsvField(LastIntrabarDirection)+",";
   row+=DoubleToString(LastIntrabarMoveAtr,6)+",";
   row+=DoubleToString(LastIntrabarReboundAtr,6)+",";
   row+=DoubleToString(LastIntrabarMinStrength,6)+",";
   row+=DoubleToString(LastIntrabarMinMoveAtr,6)+",";
   row+=DoubleToString(LastIntrabarMinReboundAtr,6)+",";
   row+=CsvField(BoolText(LastAiTrendConfirmed))+",";
   row+=CsvField(LastAiTrendDirection)+",";
   row+=DoubleToString(LastAiTrendScore,6)+",";
   row+=DoubleToString(LastAiTrendMoveAtr,6)+",";
   row+=DoubleToString(LastAiTrendConsistency,6)+",";
   row+=DoubleToString(LastTrendMinPathAtr,6)+",";
   row+=DoubleToString(LastTrendMinConsistency,6)+",";
   row+=DoubleToString(LastTrendMinEdgeFraction,6)+",";
   row+=DoubleToString(LastTrendMinMicroMoveAtr,6)+",";
   row+=DoubleToString(LastStopDistance,_Digits)+",";
   row+=DoubleToString(LastTargetDistance,_Digits)+",";
   row+=CsvField(BoolText(EnableLiveTrading))+",";
   row+=CsvField(BoolText(AccountLockHealthy()))+",";
   row+=CsvField(AccountTypeText())+",";
   row+=CsvField(AccountInfoString(ACCOUNT_CURRENCY))+",";
   row+=DoubleToString(AccountInfoDouble(ACCOUNT_BALANCE),2)+",";
   row+=DoubleToString(AccountUnitsToUSD(AccountInfoDouble(ACCOUNT_BALANCE)),4)+",";
   row+=DoubleToString(RiskPerTradeUSD,4)+",";
   row+=DoubleToString(MoneyUnitsPerUSD,4)+",";
   row+=DoubleToString(LastRiskBudgetUnits,4)+",";
   row+=CsvField(BoolText(AllowMinLotRiskOverride))+",";
   row+=DoubleToString(MaxExecutableRiskUSD,4)+",";
   row+=CsvField(BoolText(LastMinLotOverrideUsed))+",";
   row+=CsvField(LastSizingSide)+",";
   row+=DoubleToString(LastPlannedVolume,4)+",";
   row+=DoubleToString(LastEstimatedStopLossUnits,4)+",";
   row+=DoubleToString(AccountUnitsToUSD(LastEstimatedStopLossUnits),4)+",";
   row+=DoubleToString(LastMinimumLotStopLossUnits,4)+",";
   row+=DoubleToString(AccountUnitsToUSD(LastMinimumLotStopLossUnits),4)+",";
   row+=CsvField(BoolText(MinimumLotExceedsRiskBudget()))+"\r\n";

   FileWriteString(handle,row);
   FileFlush(handle);
   FileClose(handle);
}

void AppendTradeCsv(const ulong deal)
{
   if(!WriteCsvLogs || StringLen(EffectiveTradeCsvFileName())==0 || deal==0 || !HistoryDealSelect(deal))
      return;
   if(HistoryDealGetString(deal,DEAL_SYMBOL)!=_Symbol)
      return;
   if((ulong)HistoryDealGetInteger(deal,DEAL_MAGIC)!=MagicNumber)
      return;

   int handle=FileOpen(
      EffectiveTradeCsvFileName(),
      FILE_READ|FILE_WRITE|FILE_CSV|FILE_ANSI|FILE_COMMON,
      ','
   );
   if(handle==INVALID_HANDLE)
   {
      Print("Ramon trade CSV open failed err=",GetLastError());
      return;
   }
   bool empty=(FileSize(handle)==0);
   FileSeek(handle,0,SEEK_END);
   if(empty)
   {
      FileWrite(handle,
         "captured","signal_bar_time","sample_key","deal","position_id","entry","type",
         "volume","price","profit_units","commission_units","swap_units",
         "account_currency","comment");
   }

   long entry=HistoryDealGetInteger(deal,DEAL_ENTRY);
   long type=HistoryDealGetInteger(deal,DEAL_TYPE);
   string entry_text=(entry==DEAL_ENTRY_IN ? "IN" :
      (entry==DEAL_ENTRY_OUT ? "OUT" :
      (entry==DEAL_ENTRY_INOUT ? "INOUT" : "OUT_BY")));
   string type_text=(type==DEAL_TYPE_BUY ? "BUY" :
      (type==DEAL_TYPE_SELL ? "SELL" : IntegerToString(type)));

   string deal_comment=HistoryDealGetString(deal,DEAL_COMMENT);
   string deal_sample_key="";
   int ramon_pos=StringFind(deal_comment,"Ramon:");
   if(ramon_pos>=0 && StringLen(deal_comment)>=ramon_pos+22)
      deal_sample_key=StringSubstr(deal_comment,ramon_pos+6,16);

   FileWrite(handle,
      TimeToString(TimeCurrent(),TIME_DATE|TIME_SECONDS),
      (LastSignalBarTime>0 ? TimeToString(LastSignalBarTime,TIME_DATE|TIME_MINUTES) : "NONE"),
      deal_sample_key,
      IntegerToString((long)deal),
      IntegerToString(HistoryDealGetInteger(deal,DEAL_POSITION_ID)),
      entry_text,type_text,
      DoubleToString(HistoryDealGetDouble(deal,DEAL_VOLUME),4),
      DoubleToString(HistoryDealGetDouble(deal,DEAL_PRICE),_Digits),
      DoubleToString(HistoryDealGetDouble(deal,DEAL_PROFIT),4),
      DoubleToString(HistoryDealGetDouble(deal,DEAL_COMMISSION),4),
      DoubleToString(HistoryDealGetDouble(deal,DEAL_SWAP),4),
      AccountInfoString(ACCOUNT_CURRENCY),
      HistoryDealGetString(deal,DEAL_COMMENT));
   FileFlush(handle);
   FileClose(handle);
}

bool ValidSampleKey(const string key)
{
   if(StringLen(key)!=16) return false;
   for(int i=0;i<16;i++)
   {
      ushort c=StringGetCharacter(key,i);
      if(!((c>=48 && c<=57) || (c>=97 && c<=102))) return false;
   }
   return true;
}

string JsonEscape(string value)
{
   StringReplace(value,"\\","\\\\");
   StringReplace(value,"\"","\\\"");
   StringReplace(value,"\r","\\r");
   StringReplace(value,"\n","\\n");
   StringReplace(value,"\t","\\t");
   return value;
}

bool PositionIdOpen(const ulong identifier)
{
   for(int i=PositionsTotal()-1;i>=0;i--)
   {
      if(PositionGetTicket(i)==0) continue;
      if((ulong)PositionGetInteger(POSITION_IDENTIFIER)==identifier) return true;
   }
   return false;
}

// Persist event-time telemetry independently of optional CSV logging.
// Never infer a historical deal's UTC offset from the current server offset.
string DealTelemetryPath(const ulong deal)
{
   string server=AccountInfoString(ACCOUNT_SERVER);
   StringReplace(server,"\\","_"); StringReplace(server,"/","_");
   StringReplace(server,":","_");
   return "RamonTelemetry\\"+server+"_"+IntegerToString(AccountInfoInteger(ACCOUNT_LOGIN))
      +"\\"+IntegerToString((long)deal)+".csv";
}

bool ReadDealTelemetry(const ulong deal,int &offset,string &version,string &detail)
{
   int file=FileOpen(DealTelemetryPath(deal),FILE_READ|FILE_CSV|FILE_ANSI|FILE_COMMON,'\t');
   if(file==INVALID_HANDLE) return false;
   string marker=FileReadString(file);
   offset=(int)StringToInteger(FileReadString(file));
   version=FileReadString(file);
   detail=FileReadString(file);
   FileClose(file);
   return (marker=="v1" || marker=="v2") && MathAbs(offset)<=14*3600 && version!="";
}

bool ReadDealSizingTelemetry(
   const ulong deal,
   string &sample_key,
   double &risk_budget_units,
   double &planned_volume,
   double &min_lot_sl_units,
   bool &override_used,
   double &max_executable_risk_usd,
   double &money_units_per_usd
)
{
   sample_key="";
   risk_budget_units=0.0;
   planned_volume=0.0;
   min_lot_sl_units=0.0;
   override_used=false;
   max_executable_risk_usd=0.0;
   money_units_per_usd=0.0;
   int file=FileOpen(DealTelemetryPath(deal),FILE_READ|FILE_CSV|FILE_ANSI|FILE_COMMON,'\t');
   if(file==INVALID_HANDLE) return false;
   string marker=FileReadString(file);
   FileReadString(file); // UTC offset
   FileReadString(file); // EA version
   FileReadString(file); // exit detail
   if(marker!="v2")
   {
      FileClose(file);
      return false;
   }
   sample_key=FileReadString(file);
   risk_budget_units=StringToDouble(FileReadString(file));
   planned_volume=StringToDouble(FileReadString(file));
   min_lot_sl_units=StringToDouble(FileReadString(file));
   override_used=(StringToInteger(FileReadString(file))==1);
   max_executable_risk_usd=StringToDouble(FileReadString(file));
   money_units_per_usd=StringToDouble(FileReadString(file));
   FileClose(file);
   return (
      ValidSampleKey(sample_key)
      && risk_budget_units>0.0
      && planned_volume>0.0
      && min_lot_sl_units>0.0
      && max_executable_risk_usd>0.0
      && money_units_per_usd>0.0
   );
}

void RecordDealTelemetry(const ulong deal,const string close_detail="")
{
   if(deal==0 || !HistoryDealSelect(deal)) return;
   if(HistoryDealGetString(deal,DEAL_SYMBOL)!=_Symbol
      || (ulong)HistoryDealGetInteger(deal,DEAL_MAGIC)!=MagicNumber) return;
   int offset=0;
   string version="",detail="";
   bool recorded=ReadDealTelemetry(deal,offset,version,detail);
   if(!recorded)
   {
      // Only sample the clock at a fresh deal event. The terminal's OS clock must be correct.
      datetime at=(datetime)HistoryDealGetInteger(deal,DEAL_TIME);
      if(MathAbs((long)TimeCurrent()-(long)at)>60) return;
      long delta=(long)TimeCurrent()-(long)TimeGMT();
      // Broker zones use quarter-hour increments; discard stale/ambiguous clock samples.
      offset=(int)(MathRound((double)delta/900.0)*900.0);
      if(MathAbs(offset)>14*3600 || MathAbs(delta-offset)>30) return;
      version="0.54.9";
   }
   if(close_detail!="") detail=close_detail;

   string sizing_sample="";
   double risk_budget_units=0.0,planned_volume=0.0,min_lot_sl_units=0.0;
   double max_executable_risk_usd=0.0,money_units_per_usd=0.0;
   bool override_used=false;
   bool has_sizing=ReadDealSizingTelemetry(
      deal,sizing_sample,risk_budget_units,planned_volume,min_lot_sl_units,
      override_used,max_executable_risk_usd,money_units_per_usd
   );

   long entry_kind=HistoryDealGetInteger(deal,DEAL_ENTRY);
   string comment=HistoryDealGetString(deal,DEAL_COMMENT);
   string deal_sample=(StringFind(comment,"Ramon:")==0 ? StringSubstr(comment,6,16) : "");
   if(!has_sizing && entry_kind==DEAL_ENTRY_IN && ValidSampleKey(deal_sample)
      && deal_sample==PendingSizingSampleKey)
   {
      sizing_sample=PendingSizingSampleKey;
      risk_budget_units=PendingSizingRiskBudgetUnits;
      planned_volume=PendingSizingPlannedVolume;
      min_lot_sl_units=PendingSizingMinLotSLUnits;
      override_used=PendingSizingOverrideUsed;
      max_executable_risk_usd=PendingSizingMaxExecutableRiskUSD;
      money_units_per_usd=PendingSizingMoneyUnitsPerUSD;
      has_sizing=(
         risk_budget_units>0.0 && planned_volume>0.0 && min_lot_sl_units>0.0
         && max_executable_risk_usd>0.0 && money_units_per_usd>0.0
      );
   }

   int file=FileOpen(DealTelemetryPath(deal),FILE_WRITE|FILE_CSV|FILE_ANSI|FILE_COMMON,'\t');
   if(file==INVALID_HANDLE)
   {
      Print("Ramon deal telemetry write failed: ",GetLastError());
      return;
   }
   if(has_sizing)
   {
      FileWrite(
         file,"v2",IntegerToString(offset),version,detail,sizing_sample,
         DoubleToString(risk_budget_units,8),DoubleToString(planned_volume,8),
         DoubleToString(min_lot_sl_units,8),(override_used ? "1" : "0"),
         DoubleToString(max_executable_risk_usd,8),DoubleToString(money_units_per_usd,8)
      );
   }
   else
      FileWrite(file,"v1",IntegerToString(offset),version,detail);
   FileFlush(file);
   FileClose(file);
   if(has_sizing && sizing_sample==PendingSizingSampleKey)
      ClearPendingSizing();
}

bool ClosedTradePayload(const ulong identifier,string &payload)
{
   if(PositionIdOpen(identifier) || !HistorySelectByPosition(identifier)) return false;
   string sample="",direction="",exit_reason="";
   datetime opened=0,closed=0;
   ulong opening_deal=0,closing_deal=0;
   double in_volume=0.0,out_volume=0.0,net=0.0,risk=0.0;
   double entry_fill_value=0.0;
   double profit=0.0,commission=0.0,swap=0.0,fee=0.0;
   // Do not call HistoryDealSelect here: it resets the selected position history.
   for(int i=0;i<HistoryDealsTotal();i++)
   {
      ulong deal=HistoryDealGetTicket(i);
      if(deal==0) return false;
      if(HistoryDealGetString(deal,DEAL_SYMBOL)!=_Symbol) continue;
      profit+=HistoryDealGetDouble(deal,DEAL_PROFIT);
      commission+=HistoryDealGetDouble(deal,DEAL_COMMISSION);
      swap+=HistoryDealGetDouble(deal,DEAL_SWAP);
      fee+=HistoryDealGetDouble(deal,DEAL_FEE);
      long type=HistoryDealGetInteger(deal,DEAL_TYPE);
      if(type!=DEAL_TYPE_BUY && type!=DEAL_TYPE_SELL) continue;
      long entry=HistoryDealGetInteger(deal,DEAL_ENTRY);
      datetime at=(datetime)HistoryDealGetInteger(deal,DEAL_TIME);
      double volume=HistoryDealGetDouble(deal,DEAL_VOLUME);
      if(entry==DEAL_ENTRY_IN)
      {
         if((ulong)HistoryDealGetInteger(deal,DEAL_MAGIC)!=MagicNumber) return false;
         string comment=HistoryDealGetString(deal,DEAL_COMMENT);
         if(StringFind(comment,"Ramon:")!=0) return false;
         string key=StringSubstr(comment,6,16);
         string side=(type==DEAL_TYPE_BUY ? "BUY" : "SELL");
         if(!ValidSampleKey(key) || (sample!="" && sample!=key)
            || (direction!="" && direction!=side)) return false;
         sample=key;
         direction=side;
         if(opened==0 || at<opened) { opened=at; opening_deal=deal; }
         in_volume+=volume;
         ulong order=(ulong)HistoryDealGetInteger(deal,DEAL_ORDER);
         double stop=HistoryOrderGetDouble(order,ORDER_SL);
         double fill=HistoryDealGetDouble(deal,DEAL_PRICE);
         if(fill<=0.0) return false;
         entry_fill_value+=fill*volume;
         double loss=0.0;
         ENUM_ORDER_TYPE order_side=(type==DEAL_TYPE_BUY ? ORDER_TYPE_BUY : ORDER_TYPE_SELL);
         if(stop<=0.0 || !OrderCalcProfit(order_side,_Symbol,volume,fill,stop,loss) || loss>=0.0)
            return false;
         risk+=MathAbs(loss);
      }
      else if(entry==DEAL_ENTRY_OUT || entry==DEAL_ENTRY_OUT_BY)
      {
         out_volume+=volume;
         if(at>=closed)
         {
            closed=at;
            closing_deal=deal;
            exit_reason=EnumToString((ENUM_DEAL_REASON)HistoryDealGetInteger(deal,DEAL_REASON));
         }
      }
      else return false; // Reversals/netting multiple decisions have ambiguous attribution.
   }
   if(sample=="" || risk<=0.0 || closed<opened || in_volume<=0.0
      || entry_fill_value<=0.0
      || MathAbs(in_volume-out_volume)>0.000001) return false;
   double actual_fill_price=entry_fill_value/in_volume;
   net=profit+commission+swap+fee;
   string trade_key=AccountInfoString(ACCOUNT_SERVER)+":"
      +IntegerToString(AccountInfoInteger(ACCOUNT_LOGIN))+":"+IntegerToString((long)identifier);
   payload="{\"trade_key\":\""+JsonEscape(trade_key)+"\",\"sample_key\":\""+sample
      +"\",\"symbol\":\""+_Symbol+"\",\"direction\":\""+direction
      +"\",\"opened\":"+IntegerToString((long)opened)+",\"closed\":"+IntegerToString((long)closed)
      +",\"net_units\":"+DoubleToString(net,8)+",\"initial_risk_units\":"+DoubleToString(risk,8)
      +",\"actual_fill_price\":"+DoubleToString(actual_fill_price,_Digits)
      +",\"profit_units\":"+DoubleToString(profit,8)
      +",\"commission_units\":"+DoubleToString(commission,8)
      +",\"swap_units\":"+DoubleToString(swap,8)
      +",\"fee_units\":"+DoubleToString(fee,8)
      +",\"exit_reason\":\""+exit_reason+"\""
      +",\"trade_role\":\""+(SmallOnlyMode ? "SMALL" : "MAIN")+"\""
      +",\"entry_magic\":"+IntegerToString((long)MagicNumber);
   int offset=0;
   string version="",detail="";
   if(ReadDealTelemetry(opening_deal,offset,version,detail))
      payload+=",\"opened_utc_offset_seconds\":"+IntegerToString(offset)
         +",\"entry_ea_version\":\""+JsonEscape(version)+"\"";
   string sizing_sample="";
   double risk_budget_units=0.0,planned_volume=0.0,min_lot_sl_units=0.0;
   double max_executable_risk_usd=0.0,money_units_per_usd=0.0;
   bool override_used=false;
   if(ReadDealSizingTelemetry(
      opening_deal,sizing_sample,risk_budget_units,planned_volume,min_lot_sl_units,
      override_used,max_executable_risk_usd,money_units_per_usd
   ) && sizing_sample==sample)
   {
      payload+=",\"risk_budget_units\":"+DoubleToString(risk_budget_units,8)
         +",\"planned_volume\":"+DoubleToString(planned_volume,8)
         +",\"min_lot_sl_units\":"+DoubleToString(min_lot_sl_units,8)
         +",\"min_lot_override_used\":"+IntegerToString(override_used ? 1 : 0)
         +",\"max_executable_risk_usd\":"+DoubleToString(max_executable_risk_usd,8)
         +",\"money_units_per_usd\":"+DoubleToString(money_units_per_usd,8);
   }
   if(ReadDealTelemetry(closing_deal,offset,version,detail))
   {
      payload+=",\"closed_utc_offset_seconds\":"+IntegerToString(offset);
      if(exit_reason=="DEAL_REASON_EXPERT" && detail!="")
         payload+=",\"exit_detail\":\""+JsonEscape(detail)+"\"";
   }
   payload+="}";
   return true;
}

// Read execution history independently of learning labels, sample IDs and uploads.
// Result: -1 = unreadable, 0 = other robot/open position, 1 = our closed position.
int ReadCooldownPosition(const ulong identifier,string &direction,
                         datetime &closed,bool &sl_loss)
{
   direction="";
   closed=0;
   sl_loss=false;
   if(PositionIdOpen(identifier)) return 0;
   if(!HistorySelectByPosition(identifier)) return -1;
   bool ours=false,foreign=false,ambiguous=false;
   double in_volume=0.0,out_volume=0.0,net=0.0;
   long last_msc=-1,last_reason=-1;
   ulong last_ticket=0;
   for(int i=0;i<HistoryDealsTotal();i++)
   {
      ulong deal=HistoryDealGetTicket(i);
      if(deal==0) return -1;
      if(HistoryDealGetString(deal,DEAL_SYMBOL)!=_Symbol) continue;
      net+=HistoryDealGetDouble(deal,DEAL_PROFIT)
         +HistoryDealGetDouble(deal,DEAL_COMMISSION)
         +HistoryDealGetDouble(deal,DEAL_SWAP)
         +HistoryDealGetDouble(deal,DEAL_FEE);
      long type=HistoryDealGetInteger(deal,DEAL_TYPE);
      if(type!=DEAL_TYPE_BUY && type!=DEAL_TYPE_SELL) continue;
      long entry=HistoryDealGetInteger(deal,DEAL_ENTRY);
      double volume=HistoryDealGetDouble(deal,DEAL_VOLUME);
      if(entry==DEAL_ENTRY_IN || entry==DEAL_ENTRY_INOUT)
      {
         if((ulong)HistoryDealGetInteger(deal,DEAL_MAGIC)==MagicNumber) ours=true;
         else foreign=true;
         string side=(type==DEAL_TYPE_BUY ? "BUY" : "SELL");
         if(direction!="" && direction!=side) ambiguous=true;
         direction=side;
         in_volume+=volume;
         if(entry==DEAL_ENTRY_INOUT) ambiguous=true;
      }
      if(entry==DEAL_ENTRY_OUT || entry==DEAL_ENTRY_OUT_BY || entry==DEAL_ENTRY_INOUT)
      {
         out_volume+=volume;
         long msc=HistoryDealGetInteger(deal,DEAL_TIME_MSC);
         if(msc>last_msc || (msc==last_msc && deal>last_ticket))
         {
            last_msc=msc;
            last_ticket=deal;
            closed=(datetime)HistoryDealGetInteger(deal,DEAL_TIME);
            last_reason=HistoryDealGetInteger(deal,DEAL_REASON);
         }
      }
   }
   if(!ours) return 0;
   // Unknown/mixed/manual outcomes break the streak; never skip our position
   // merely because it would not qualify as a supervised-learning sample.
   sl_loss=(!foreign && !ambiguous && in_volume>0.0
      && MathAbs(in_volume-out_volume)<=0.000001
      && last_reason==DEAL_REASON_SL && net<0.0);
   return 1;
}

bool LocalLossCooldownBlocked(const string direction,string &reason)
{
   reason="";
   datetime now=TimeCurrent();
   if(!HistorySelect(0,now))
   { reason="loss_cooldown_history_unavailable"; return true; }
   // Snapshot IDs before HistorySelectByPosition replaces the selected deal list.
   // Sort by final exit milliseconds, independently of terminal iteration order.
   ulong identifiers[];
   long exit_times[];
   for(int i=HistoryDealsTotal()-1;i>=0;i--)
   {
      ulong deal=HistoryDealGetTicket(i);
      if(deal==0)
      { reason="loss_cooldown_history_unavailable"; return true; }
      if(HistoryDealGetString(deal,DEAL_SYMBOL)!=_Symbol) continue;
      long entry=HistoryDealGetInteger(deal,DEAL_ENTRY);
      if(entry!=DEAL_ENTRY_OUT && entry!=DEAL_ENTRY_OUT_BY && entry!=DEAL_ENTRY_INOUT) continue;
      ulong identifier=(ulong)HistoryDealGetInteger(deal,DEAL_POSITION_ID);
      long msc=HistoryDealGetInteger(deal,DEAL_TIME_MSC);
      int found=-1;
      for(int j=0;j<ArraySize(identifiers);j++) if(identifiers[j]==identifier) found=j;
      if(found>=0)
      { if(msc>exit_times[found]) exit_times[found]=msc; continue; }
      int count=ArraySize(identifiers);
      ArrayResize(identifiers,count+1);
      ArrayResize(exit_times,count+1);
      identifiers[count]=identifier;
      exit_times[count]=msc;
   }
   for(int i=0;i<ArraySize(identifiers);i++)
      for(int j=i+1;j<ArraySize(identifiers);j++)
         if(exit_times[j]>exit_times[i])
         {
            long at=exit_times[i]; exit_times[i]=exit_times[j]; exit_times[j]=at;
            ulong id=identifiers[i]; identifiers[i]=identifiers[j]; identifiers[j]=id;
         }
   int losses=0;
   datetime latest_close=0;
   for(int i=0;i<ArraySize(identifiers);i++)
   {
      string side="";
      datetime closed=0;
      bool sl_loss=false;
      int state=ReadCooldownPosition(identifiers[i],side,closed,sl_loss);
      if(state<0)
      { reason="loss_cooldown_history_unavailable"; return true; }
      if(state==0) continue;
      if(losses==0)
      {
         latest_close=closed;
         if(latest_close>now)
         { reason="loss_cooldown_history_time_invalid"; return true; }
         if(now-latest_close>=2*15*60) return false;
      }
      if(!sl_loss || side!=direction) return false;
      losses++;
      if(losses==2)
      { reason="same_direction_sl_cooldown"; return true; }
   }
   return false;
}

void SyncClosedTrades()
{
   if(!AccountLockHealthy() || AccountInfoInteger(ACCOUNT_TRADE_MODE)!=ACCOUNT_TRADE_MODE_REAL)
      return;
   datetime now=TimeCurrent();
   if(LastTradeSync>0 && now-LastTradeSync<30) return;
   LastTradeSync=now;
   // Recover unsent outcomes after service/terminal restarts from broker history.
   if(!HistorySelect(now-30*86400,now)) return;
   ulong identifiers[];
   for(int i=0;i<HistoryDealsTotal();i++)
   {
      ulong deal=HistoryDealGetTicket(i);
      if(deal==0 || HistoryDealGetString(deal,DEAL_SYMBOL)!=_Symbol
         || (ulong)HistoryDealGetInteger(deal,DEAL_MAGIC)!=MagicNumber
         || HistoryDealGetInteger(deal,DEAL_ENTRY)!=DEAL_ENTRY_IN) continue;
      ulong identifier=(ulong)HistoryDealGetInteger(deal,DEAL_POSITION_ID);
      bool seen=false;
      for(int j=0;j<ArraySize(identifiers);j++) if(identifiers[j]==identifier) seen=true;
      for(int j=0;j<ArraySize(SyncedTradeIds);j++) if(SyncedTradeIds[j]==identifier) seen=true;
      if(seen) continue;
      int count=ArraySize(identifiers);
      ArrayResize(identifiers,count+1);
      identifiers[count]=identifier;
   }
   for(int i=0;i<ArraySize(identifiers);i++)
   {
      string payload="";
      if(!ClosedTradePayload(identifiers[i],payload)) continue;
      char request[],response[];
      StringToCharArray(payload,request,0,WHOLE_ARRAY,CP_UTF8);
      ArrayResize(request,ArraySize(request)-1);
      string headers="";
      string url=StringSubstr(ModelUrl,0,StringLen(ModelUrl)-StringLen("/decision"))+"/trades";
      int code=WebRequest("POST",url,"Content-Type: application/json\r\n",
         1000,request,response,headers);
      if(code==200)
      {
         int count=ArraySize(SyncedTradeIds);
         ArrayResize(SyncedTradeIds,count+1);
         SyncedTradeIds[count]=identifiers[i];
         TradeLearningStatus="SYNCED "+IntegerToString((long)identifiers[i]);
      }
      else TradeLearningStatus="RETRY HTTP "+IntegerToString(code);
      return; // At most one bounded telemetry request per timer cycle.
   }
}

string TPPlanGlobalKey(const string sample_key,const string suffix)
{
   return "RamonTP."+IntegerToString(AccountInfoInteger(ACCOUNT_LOGIN))+"."+sample_key+"."+suffix;
}

bool ValidDirectionalTargets(
   const string direction,const double entry,
   const double tp1,const double tp2,const double tp3
)
{
   if(entry<=0.0 || tp1<=0.0 || tp2<=0.0 || tp3<=0.0)
      return false;
   if(direction=="BUY")
      return entry<tp1 && tp1<tp2 && tp2<tp3;
   if(direction=="SELL")
      return entry>tp1 && tp1>tp2 && tp2>tp3;
   return false;
}

void PersistTPPlan(
   const string sample_key,const string direction,
   const double entry,const double initial_sl,
   const double tp1,const double tp2,const double tp3
)
{
   if(!EnableTPStageManagement || !ValidSampleKey(sample_key)
      || initial_sl<=0.0
      || (direction=="BUY" && initial_sl>=entry)
      || (direction=="SELL" && initial_sl<=entry))
      return;
   GlobalVariableSet(TPPlanGlobalKey(sample_key,"DIR"),direction=="BUY" ? 1.0 : -1.0);
   GlobalVariableSet(TPPlanGlobalKey(sample_key,"ISL"),initial_sl);
   if(!ValidDirectionalTargets(direction,entry,tp1,tp2,tp3))
      return; // Early R-lock remains available even when no TP-stage plan exists.
   GlobalVariableSet(TPPlanGlobalKey(sample_key,"TP1"),tp1);
   GlobalVariableSet(TPPlanGlobalKey(sample_key,"TP2"),tp2);
   GlobalVariableSet(TPPlanGlobalKey(sample_key,"TP3"),tp3);
   GlobalVariableSet(TPPlanGlobalKey(sample_key,"STAGE"),0.0);
   GlobalVariableSet(TPPlanGlobalKey(sample_key,"HIT"),0.0);
}

void ResetTPStageRuntime()
{
   TPStagePositionIdentifier=0;
   TPStageSampleKey="";
   TPStageDirection="NONE";
   TPStageTP1=0.0;
   TPStageTP2=0.0;
   TPStageTP3=0.0;
   TPStage=0;
   TPStageHitTime=0;
   TPStageWeakSnapshots=0;
   TPStageLastDecisionTime=LastModelSnapshotTime;
   TPStageProgress=0.0;
   TPStageStatus="INACTIVE";
   TPStageLockedSL=0.0;
   TPStageLockStatus="INACTIVE";
}

bool LoadTPStagePlan(const ulong ticket)
{
   if(!EnableTPStageManagement || ticket==0 || !PositionSelectByTicket(ticket))
      return false;

   ulong identifier=(ulong)PositionGetInteger(POSITION_IDENTIFIER);
   if(identifier==0)
      return false;
   if(TPStagePositionIdentifier==identifier && ValidSampleKey(TPStageSampleKey))
      return true;
   ResetTPStageRuntime();

   string comment=PositionGetString(POSITION_COMMENT);
   string sample_key=(StringFind(comment,"Ramon:")==0 ? StringSubstr(comment,6,16) : "");
   if(!ValidSampleKey(sample_key))
   {
      TPStageStatus="NO_PLAN";
      return false;
   }

   string kdir=TPPlanGlobalKey(sample_key,"DIR");
   string ktp1=TPPlanGlobalKey(sample_key,"TP1");
   string ktp2=TPPlanGlobalKey(sample_key,"TP2");
   string ktp3=TPPlanGlobalKey(sample_key,"TP3");
   if(!GlobalVariableCheck(kdir) || !GlobalVariableCheck(ktp1)
      || !GlobalVariableCheck(ktp2) || !GlobalVariableCheck(ktp3))
   {
      TPStageStatus="NO_PLAN";
      return false;
   }

   string direction=(GlobalVariableGet(kdir)>=0.0 ? "BUY" : "SELL");
   double tp1=GlobalVariableGet(ktp1);
   double tp2=GlobalVariableGet(ktp2);
   double tp3=GlobalVariableGet(ktp3);
   double entry=PositionGetDouble(POSITION_PRICE_OPEN);
   if(!ValidDirectionalTargets(direction,entry,tp1,tp2,tp3))
   {
      TPStageStatus="INVALID_PLAN";
      return false;
   }

   TPStagePositionIdentifier=identifier;
   TPStageSampleKey=sample_key;
   TPStageDirection=direction;
   TPStageTP1=tp1;
   TPStageTP2=tp2;
   TPStageTP3=tp3;
   TPStage=(GlobalVariableCheck(TPPlanGlobalKey(sample_key,"STAGE"))
      ? (int)GlobalVariableGet(TPPlanGlobalKey(sample_key,"STAGE")) : 0);
   TPStageHitTime=(GlobalVariableCheck(TPPlanGlobalKey(sample_key,"HIT"))
      ? (datetime)GlobalVariableGet(TPPlanGlobalKey(sample_key,"HIT")) : 0);
   TPStageWeakSnapshots=0;
   TPStageLastDecisionTime=LastModelSnapshotTime;
   TPStageProgress=0.0;
   TPStageStatus="ACTIVE";
   return true;
}

bool TargetReached(const string direction,const double exit_price,const double target)
{
   return (direction=="BUY" ? exit_price>=target : exit_price<=target);
}

void MarkTPStageReached(const int stage,const datetime now,const ulong ticket)
{
   TPStage=stage;
   TPStageHitTime=now;
   TPStageWeakSnapshots=0;
   TPStageLastDecisionTime=LastModelSnapshotTime;
   GlobalVariableSet(TPPlanGlobalKey(TPStageSampleKey,"STAGE"),(double)stage);
   GlobalVariableSet(TPPlanGlobalKey(TPStageSampleKey,"HIT"),(double)now);
   Print("Ramon TP STAGE ",stage," hit ticket=",ticket,
      " target=",DoubleToString(stage==1 ? TPStageTP1 : TPStageTP2,_Digits));
}

bool ProtectReachedTPStage(const ulong ticket)
{
   if(TPStage<1 || ticket==0 || !PositionSelectByTicket(ticket))
      return false;

   MqlTick tick;
   if(!SymbolInfoTick(_Symbol,tick))
      return false;

   double entry=PositionGetDouble(POSITION_PRICE_OPEN);
   double current_sl=PositionGetDouble(POSITION_SL);
   double current_tp=PositionGetDouble(POSITION_TP);
   double point=SymbolInfoDouble(_Symbol,SYMBOL_POINT);
   if(entry<=0.0 || point<=0.0)
      return false;

   long stops_points=SymbolInfoInteger(_Symbol,SYMBOL_TRADE_STOPS_LEVEL);
   long freeze_points=SymbolInfoInteger(_Symbol,SYMBOL_TRADE_FREEZE_LEVEL);
   double min_distance=(double)MathMax(stops_points,freeze_points)*point+2.0*point;
   double stage_target=(TPStage>=2 ? TPStageTP2 : TPStageTP1);
   double desired_sl=0.0;

   if(TPStageDirection=="BUY")
   {
      double legal_max=tick.bid-min_distance;
      desired_sl=MathMin(stage_target,legal_max);
      if(desired_sl<=entry+point)
      {
         TPStageLockStatus="WAIT_BROKER_DISTANCE";
         return false;
      }
      desired_sl=NormalizeDouble(desired_sl,_Digits);
      if(current_sl>0.0 && current_sl>=desired_sl-point*0.5)
      {
         TPStageLockedSL=current_sl;
         TPStageLockStatus="PROTECTED";
         return true;
      }
   }
   else if(TPStageDirection=="SELL")
   {
      double legal_min=tick.ask+min_distance;
      desired_sl=MathMax(stage_target,legal_min);
      if(desired_sl>=entry-point)
      {
         TPStageLockStatus="WAIT_BROKER_DISTANCE";
         return false;
      }
      desired_sl=NormalizeDouble(desired_sl,_Digits);
      if(current_sl>0.0 && current_sl<=desired_sl+point*0.5)
      {
         TPStageLockedSL=current_sl;
         TPStageLockStatus="PROTECTED";
         return true;
      }
   }
   else
      return false;

   ResetLastError();
   if(Trade.PositionModify(ticket,desired_sl,current_tp))
   {
      TPStageLockedSL=desired_sl;
      TPStageLockStatus=(TPStage>=2 ? "TP2_LOCKED" : "TP1_LOCKED");
      Print("Ramon TP PROFIT LOCK ticket=",ticket,
         " stage=",TPStage,
         " sl=",DoubleToString(desired_sl,_Digits),
         " tp=",DoubleToString(current_tp,_Digits));
      return true;
   }

   TPStageLockStatus="LOCK_RETRY "+IntegerToString((int)Trade.ResultRetcode());
   return false;
}

bool LoadEarlyProfitPlan(const ulong ticket,string &direction,double &initial_sl)
{
   direction="";
   initial_sl=0.0;
   if(ticket==0 || !PositionSelectByTicket(ticket))
      return false;
   string comment=PositionGetString(POSITION_COMMENT);
   string sample_key=(StringFind(comment,"Ramon:")==0 ? StringSubstr(comment,6,16) : "");
   if(!ValidSampleKey(sample_key))
      return false;
   string kdir=TPPlanGlobalKey(sample_key,"DIR");
   string kisl=TPPlanGlobalKey(sample_key,"ISL");
   if(!GlobalVariableCheck(kdir) || !GlobalVariableCheck(kisl))
      return false;
   direction=(GlobalVariableGet(kdir)>=0.0 ? "BUY" : "SELL");
   initial_sl=GlobalVariableGet(kisl);
   long position_type=PositionGetInteger(POSITION_TYPE);
   return initial_sl>0.0
      && ((direction=="BUY" && position_type==POSITION_TYPE_BUY)
         || (direction=="SELL" && position_type==POSITION_TYPE_SELL));
}

bool ProtectEarlyProfit(const ulong ticket,const MqlTick &tick)
{
   if(!EnableEarlyProfitLock || SmallOnlyMode || ticket==0 || !PositionSelectByTicket(ticket))
      return false;

   string direction="";
   double initial_sl=0.0;
   if(!LoadEarlyProfitPlan(ticket,direction,initial_sl))
      return false;
   double entry=PositionGetDouble(POSITION_PRICE_OPEN);
   double current_sl=PositionGetDouble(POSITION_SL);
   double current_tp=PositionGetDouble(POSITION_TP);
   double point=SymbolInfoDouble(_Symbol,SYMBOL_POINT);
   double risk_distance=MathAbs(entry-initial_sl);
   if(entry<=0.0 || point<=0.0 || risk_distance<=point
      || (direction=="BUY" && initial_sl>=entry)
      || (direction=="SELL" && initial_sl<=entry))
      return false;

   double exit_price=(direction=="BUY" ? tick.bid : tick.ask);
   double current_r=(direction=="BUY"
      ? exit_price-entry : entry-exit_price)/risk_distance;
   if(current_r<EarlyProfitLockActivation1R)
   {
      TPStageLockStatus="EARLY_WAIT";
      return false;
   }

   double lock_r=(current_r>=EarlyProfitLockActivation2R
      ? EarlyProfitLockStage2R : EarlyProfitLockStage1R);
   long stops_points=SymbolInfoInteger(_Symbol,SYMBOL_TRADE_STOPS_LEVEL);
   long freeze_points=SymbolInfoInteger(_Symbol,SYMBOL_TRADE_FREEZE_LEVEL);
   double min_distance=(double)MathMax(stops_points,freeze_points)*point+2.0*point;
   double desired_sl=entry+(direction=="BUY" ? 1.0 : -1.0)*lock_r*risk_distance;

   if(direction=="BUY")
   {
      desired_sl=MathMin(desired_sl,tick.bid-min_distance);
      if(desired_sl<=entry+point)
      {
         TPStageLockStatus="EARLY_WAIT_BROKER_DISTANCE";
         return false;
      }
      desired_sl=NormalizeDouble(desired_sl,_Digits);
      if(current_sl>0.0 && current_sl>=desired_sl-point*0.5)
      {
         TPStageLockedSL=current_sl;
         TPStageLockStatus="EARLY_PROTECTED";
         return true;
      }
   }
   else if(direction=="SELL")
   {
      desired_sl=MathMax(desired_sl,tick.ask+min_distance);
      if(desired_sl>=entry-point)
      {
         TPStageLockStatus="EARLY_WAIT_BROKER_DISTANCE";
         return false;
      }
      desired_sl=NormalizeDouble(desired_sl,_Digits);
      if(current_sl>0.0 && current_sl<=desired_sl+point*0.5)
      {
         TPStageLockedSL=current_sl;
         TPStageLockStatus="EARLY_PROTECTED";
         return true;
      }
   }
   else
      return false;

   ResetLastError();
   if(Trade.PositionModify(ticket,desired_sl,current_tp))
   {
      TPStageLockedSL=desired_sl;
      TPStageLockStatus=(lock_r>=EarlyProfitLockStage2R
         ? "EARLY_02R_LOCKED" : "EARLY_BE_PLUS_LOCKED");
      Print("Ramon EARLY PROFIT LOCK ticket=",ticket,
         " current_r=",DoubleToString(current_r,3),
         " lock_r=",DoubleToString(lock_r,3),
         " sl=",DoubleToString(desired_sl,_Digits));
      return true;
   }

   TPStageLockStatus="EARLY_LOCK_RETRY "+IntegerToString((int)Trade.ResultRetcode());
   return false;
}

void ObserveTPStageCrossingsOnTick()
{
   if(SmallOnlyMode)
      return;

   ulong ticket=0;
   datetime opened=0;
   if(!ManagedPosition(ticket,opened))
      return;

   bool has_stage_plan=LoadTPStagePlan(ticket);

   MqlTick tick;
   if(!SymbolInfoTick(_Symbol,tick))
      return;

   if(!has_stage_plan)
   {
      ProtectEarlyProfit(ticket,tick);
      return;
   }

   double exit_price=(TPStageDirection=="BUY" ? tick.bid : tick.ask);
   datetime now=TimeCurrent();
   if(TPStage<1 && TargetReached(TPStageDirection,exit_price,TPStageTP1))
      MarkTPStageReached(1,now,ticket);
   if(TPStage<2 && TargetReached(TPStageDirection,exit_price,TPStageTP2))
      MarkTPStageReached(2,now,ticket);

   if(TPStage>=1)
      ProtectReachedTPStage(ticket);
   else
      ProtectEarlyProfit(ticket,tick);
}

double DirectionalProgress(
   const string direction,const double from_price,const double to_price,const double current_price
)
{
   double span=MathAbs(to_price-from_price);
   if(span<=0.0) return 0.0;
   double moved=(direction=="BUY" ? current_price-from_price : from_price-current_price);
   return moved/span;
}

bool TPStageModelSupportive()
{
   bool direction_support=(LastModelDecision==TPStageDirection);
   bool intrabar_support=(LastIntrabarConfirmed && LastIntrabarDirection==TPStageDirection);
   bool trend_support=(LastAiTrendConfirmed && LastAiTrendDirection==TPStageDirection);
   return direction_support && (intrabar_support || trend_support);
}

void ResetMarketClosedExitPause()
{
   MarketClosedExitPause=false;
   MarketClosedExitPauseTickTime=0;
   MarketClosedExitPauseTicket=0;
}

bool ManagedExitPausedForMarketClosed(const ulong ticket)
{
   if(!MarketClosedExitPause)
      return false;
   if(ticket==0 || ticket!=MarketClosedExitPauseTicket)
   {
      ResetMarketClosedExitPause();
      return false;
   }

   MqlTick tick;
   if(!SymbolInfoTick(_Symbol,tick) || tick.time<=MarketClosedExitPauseTickTime)
      return true;

   Print("Ramon exit retry resumed after fresh tick ticket=",ticket,
      " old_tick=",TimeToString(MarketClosedExitPauseTickTime,TIME_DATE|TIME_SECONDS),
      " new_tick=",TimeToString(tick.time,TIME_DATE|TIME_SECONDS));
   ResetMarketClosedExitPause();
   return false;
}

void HandleManagedExitFailure(const ulong ticket,const string prefix)
{
   uint retcode=Trade.ResultRetcode();
   if(retcode==TRADE_RETCODE_MARKET_CLOSED)
   {
      MqlTick tick;
      datetime tick_time=0;
      if(SymbolInfoTick(_Symbol,tick))
         tick_time=tick.time;

      bool first_pause=(
         !MarketClosedExitPause
         || MarketClosedExitPauseTicket!=ticket
         || MarketClosedExitPauseTickTime!=tick_time
      );
      MarketClosedExitPause=true;
      MarketClosedExitPauseTicket=ticket;
      MarketClosedExitPauseTickTime=tick_time;
      StatusLine="EXIT PAUSED: MARKET CLOSED";
      if(first_pause)
         Print("Ramon execution: ",StatusLine,
            " ticket=",ticket,
            " retry only after fresh tick");
      return;
   }

   StatusLine=prefix+" FAILED "+IntegerToString((int)retcode);
   Print("Ramon execution: ",StatusLine);
}

bool ManageTPStages(const ulong ticket)
{
   if(!LoadTPStagePlan(ticket))
      return false;

   MqlTick tick;
   if(!SymbolInfoTick(_Symbol,tick))
      return false;
   double exit_price=(TPStageDirection=="BUY" ? tick.bid : tick.ask);
   datetime now=TimeCurrent();

   if(TPStage<1 && TargetReached(TPStageDirection,exit_price,TPStageTP1))
      MarkTPStageReached(1,now,ticket);
   if(TPStage<2 && TargetReached(TPStageDirection,exit_price,TPStageTP2))
      MarkTPStageReached(2,now,ticket);
   if(TPStage>=1)
      ProtectReachedTPStage(ticket);
   if(TargetReached(TPStageDirection,exit_price,TPStageTP3))
   {
      TPStage=3;
      TPStageStatus="TP3_EXIT";
      if(ManagedExitPausedForMarketClosed(ticket))
      {
         StatusLine="EXIT PAUSED: MARKET CLOSED";
         return true;
      }
      if(Trade.PositionClose(ticket,MaxDeviationPoints))
      {
         ResetMarketClosedExitPause();
         RecordDealTelemetry(Trade.ResultDeal(),"tp3_stage_exit");
         StatusLine="TP3 STAGE EXIT";
         Print("Ramon execution: ",StatusLine);
         return true;
      }
      HandleManagedExitFailure(ticket,"TP3 STAGE EXIT");
      return true;
   }

   if(TPStage==0)
   {
      TPStageStatus="BEFORE_TP1";
      TPStageProgress=DirectionalProgress(
         TPStageDirection,
         PositionGetDouble(POSITION_PRICE_OPEN),
         TPStageTP1,
         exit_price
      );
      return false;
   }

   double next_target=(TPStage==1 ? TPStageTP2 : TPStageTP3);
   double base_target=(TPStage==1 ? TPStageTP1 : TPStageTP2);
   TPStageProgress=DirectionalProgress(TPStageDirection,base_target,next_target,exit_price);
   bool fresh_model=(LastModelSnapshotTime>0
      && LastModelSnapshotTime>=TPStageHitTime
      && LastModelSnapshotTime<=now
      && now-LastModelSnapshotTime<=SnapshotIntervalSeconds*2+15
      && (LastModelDecision=="BUY" || LastModelDecision=="SELL" || LastModelDecision=="WAIT"));
   bool supportive=(fresh_model && TPStageModelSupportive());
   if(!fresh_model)
      TPStageWeakSnapshots=0;
   else if(TPStageLastDecisionTime!=LastModelSnapshotTime)
   {
      TPStageLastDecisionTime=LastModelSnapshotTime;
      if(supportive) TPStageWeakSnapshots=0;
      else TPStageWeakSnapshots++;
   }

   int grace=(TPStage==1 ? TP1GraceSeconds : TP2GraceSeconds);
   double retrace_fraction=(TPStage==1 ? TP1RetraceFraction : TP2RetraceFraction);
   double stage_span=MathAbs(next_target-base_target);
   double retrace_price=(TPStageDirection=="BUY"
      ? base_target-stage_span*retrace_fraction
      : base_target+stage_span*retrace_fraction);
   bool retraced=(TPStageDirection=="BUY" ? exit_price<=retrace_price : exit_price>=retrace_price);
   bool grace_over=(TPStageHitTime>0 && now-TPStageHitTime>=grace);
   bool weak_exit=(
      grace_over
      && fresh_model
      && TPStageWeakSnapshots>=TPStageWeakSnapshotsRequired
      && (TPStage==2 || TPStageProgress<TP1HealthyProgressFraction)
   );

   TPStageStatus=(supportive ? "CONTINUE" : "WATCH");
   if(grace_over && (retraced || weak_exit))
   {
      string detail=(TPStage==1 ? "tp1_stall_exit" : "tp2_stall_exit");
      TPStageStatus=(retraced ? "RETRACE_EXIT" : "WEAK_EXIT");
      if(ManagedExitPausedForMarketClosed(ticket))
      {
         StatusLine="EXIT PAUSED: MARKET CLOSED";
         return true;
      }
      if(Trade.PositionClose(ticket,MaxDeviationPoints))
      {
         ResetMarketClosedExitPause();
         RecordDealTelemetry(Trade.ResultDeal(),detail);
         StatusLine=(TPStage==1 ? "TP1->TP2 SMART EXIT" : "TP2->TP3 SMART EXIT");
         Print("Ramon execution: ",StatusLine,
            " progress=",DoubleToString(TPStageProgress,3),
            " weak=",TPStageWeakSnapshots,
            " supportive=",BoolText(supportive));
         return true;
      }
      HandleManagedExitFailure(ticket,"TP STAGE EXIT");
      return true;
   }
   return false;
}

void ResetMainFastProfitState()
{
   MainFastProfitTicket=0;
   MainFastProfitWeakSnapshots=0;
   MainFastProfitLastDecisionTime=0;
   MainFastProfitProgress=0.0;
   MainFastProfitStatus="INACTIVE";
}

bool ManageMainFastProfit(const ulong ticket,const datetime opened)
{
   if(!EnableMainFastProfit || SmallOnlyMode || ticket==0 || !PositionSelectByTicket(ticket))
      return false;

   if(MainFastProfitTicket!=ticket)
   {
      ResetMainFastProfitState();
      MainFastProfitTicket=ticket;
   }

   int age=iBarShift(_Symbol,PERIOD_M15,opened,false);
   if(age<MainFastProfitMinAgeBars)
   {
      MainFastProfitStatus="WAIT_AGE";
      return false;
   }

   double current_units=PositionGetDouble(POSITION_PROFIT);
   if(current_units<MainFastProfitMinProfitUnits)
   {
      MainFastProfitStatus="WAIT_PROFIT";
      MainFastProfitWeakSnapshots=0;
      return false;
   }

   long type=PositionGetInteger(POSITION_TYPE);
   string direction=(type==POSITION_TYPE_BUY ? "BUY" : "SELL");
   double entry=PositionGetDouble(POSITION_PRICE_OPEN);
   MqlTick tick;
   if(!SymbolInfoTick(_Symbol,tick))
      return false;
   double exit_price=(direction=="BUY" ? tick.bid : tick.ask);

   // Prefer the learned TP1 plan when available; otherwise fall back to broker TP.
   double target=0.0;
   if(LoadTPStagePlan(ticket) && TPStageTP1>0.0)
      target=TPStageTP1;
   else
      target=PositionGetDouble(POSITION_TP);
   if(target<=0.0 || entry<=0.0)
   {
      MainFastProfitStatus="NO_TARGET";
      return false;
   }

   MainFastProfitProgress=DirectionalProgress(direction,entry,target,exit_price);
   if(MainFastProfitProgress>=MainFastProfitMinProgressToTP1)
   {
      MainFastProfitStatus="PROGRESS_OK";
      MainFastProfitWeakSnapshots=0;
      return false;
   }

   datetime now=TimeCurrent();
   if(LastModelSnapshotTime<=0 || LastModelSnapshotTime<opened || LastModelSnapshotTime>now
      || now-LastModelSnapshotTime>SnapshotIntervalSeconds*2+15)
   {
      MainFastProfitWeakSnapshots=0;
      MainFastProfitStatus="STALE_MODEL";
      return false;
   }

   if(MainFastProfitLastDecisionTime!=LastModelSnapshotTime)
   {
      MainFastProfitLastDecisionTime=LastModelSnapshotTime;
      bool model_support=(LastModelDecision==direction);
      bool intrabar_support=(LastIntrabarConfirmed && LastIntrabarDirection==direction);
      bool trend_support=(LastAiTrendConfirmed && LastAiTrendDirection==direction);
      bool weak=(!model_support && !intrabar_support && !trend_support);

      if(weak)
         MainFastProfitWeakSnapshots++;
      else
         MainFastProfitWeakSnapshots=0;

      Print("Ramon MAIN FAST PROFIT check ticket=",ticket,
         " age=",age,
         " profit=",DoubleToString(current_units,2),
         " progress=",DoubleToString(MainFastProfitProgress,3),
         " weak=",IntegerToString(MainFastProfitWeakSnapshots),"/",
            IntegerToString(MainFastProfitWeakSnapshotsRequired));
   }

   if(MainFastProfitWeakSnapshots<MainFastProfitWeakSnapshotsRequired)
   {
      MainFastProfitStatus="WATCH";
      return false;
   }

   if(ManagedExitPausedForMarketClosed(ticket))
   {
      MainFastProfitStatus="PAUSED_MARKET_CLOSED";
      StatusLine="MAIN FAST PROFIT PAUSED: MARKET CLOSED";
      return true;
   }

   if(Trade.PositionClose(ticket,MaxDeviationPoints))
   {
      ResetMarketClosedExitPause();
      RecordDealTelemetry(Trade.ResultDeal(),"main_fast_profit");
      MainFastProfitStatus="EXIT";
      StatusLine="MAIN FAST PROFIT EXIT profit="
         +DoubleToString(current_units,2)
         +" progress="+DoubleToString(MainFastProfitProgress*100.0,1)+"%";
      Print("Ramon execution: ",StatusLine);
      return true;
   }

   HandleManagedExitFailure(ticket,"MAIN FAST PROFIT EXIT");
   return true;
}

void ResetProfitProtectionState()
{
   ProfitProtectionTicket=0;
   ProfitProtectionPeakUnits=0.0;
   ProfitProtectionCurrentUnits=0.0;
   ProfitProtectionGivebackNowUnits=0.0;
   ProfitProtectionInitialRiskUnits=0.0;
   ProfitProtectionActivationUnits=ProfitProtectionFallbackActivationUnits;
   ProfitProtectionGivebackUnits=MathMax(
      ProfitProtectionGivebackMinUnits,
      MathMin(ProfitProtectionGivebackMaxUnits,
         ProfitProtectionFallbackActivationUnits*ProfitProtectionGivebackFraction)
   );
   ProfitProtectionArmed=false;
   ProfitProtectionShadowTriggered=false;
   ProfitProtectionShadowTriggerTime=0;
   EarlyReversalShadowTriggered=false;
   EarlyReversalShadowTriggerTime=0;
}

double ManagedPositionInitialRiskUnits(const ulong ticket)
{
   if(ticket==0 || !PositionSelectByTicket(ticket))
      return 0.0;

   double entry=PositionGetDouble(POSITION_PRICE_OPEN);
   double stop=PositionGetDouble(POSITION_SL);
   double volume=PositionGetDouble(POSITION_VOLUME);
   if(entry<=0.0 || stop<=0.0 || volume<=0.0)
      return 0.0;

   long position_type=PositionGetInteger(POSITION_TYPE);
   ENUM_ORDER_TYPE side=(position_type==POSITION_TYPE_BUY ? ORDER_TYPE_BUY : ORDER_TYPE_SELL);
   double money=0.0;
   if(!OrderCalcProfit(side,_Symbol,volume,entry,stop,money) || money>=0.0)
      return 0.0;
   return MathAbs(money);
}

bool EnforceSmallPositionRiskCap(const ulong ticket)
{
   if(!IsSmallProfitPosition(ticket) || !PositionSelectByTicket(ticket))
      return false;

   double cap=SmallProfitRiskCapUnits();
   double risk=ManagedPositionInitialRiskUnits(ticket);
   if(cap<=0.0 || risk<=cap+0.00001)
      return false;

   Print("Ramon SMALL RISK GUARD ticket=",ticket,
      " broker_sl_risk=",DoubleToString(risk,2),
      " cap=",DoubleToString(cap,2));

   if(ManagedExitPausedForMarketClosed(ticket))
   {
      StatusLine="SMALL RISK GUARD PAUSED: MARKET CLOSED";
      return true;
   }

   if(Trade.PositionClose(ticket,MaxDeviationPoints))
   {
      ResetMarketClosedExitPause();
      RecordDealTelemetry(Trade.ResultDeal(),"small_risk_guard");
      StatusLine="SMALL RISK GUARD EXIT risk="
         +DoubleToString(risk,2)
         +" cap="+DoubleToString(cap,2);
      Print("Ramon execution: ",StatusLine);
      return true;
   }

   HandleManagedExitFailure(ticket,"SMALL RISK GUARD EXIT");
   return true;
}

void ResetEarlyAdverseState()
{
   EarlyAdverseTicket=0;
   EarlyAdverseInitialRiskUnits=0.0;
   EarlyAdverseTriggerLossUnits=0.0;
   EarlyAdverseAppliedRiskFraction=0.0;
   EarlyAdverseWeakSnapshots=0;
   EarlyAdverseLastDecisionTime=0;
   EarlyAdverseTriggered=false;
}

bool ManageEarlyAdverseExit(const ulong ticket,const datetime opened)
{
   if(!EnableEarlyAdverseExit || ticket==0 || !PositionSelectByTicket(ticket))
      return false;

   if(EarlyAdverseTicket!=ticket)
   {
      ResetEarlyAdverseState();
      EarlyAdverseTicket=ticket;
      EarlyAdverseInitialRiskUnits=ManagedPositionInitialRiskUnits(ticket);
      if(IsSmallProfitPosition(ticket))
         EarlyAdverseInitialRiskUnits=MathMin(
            EarlyAdverseInitialRiskUnits,SmallProfitRiskCapUnits()
         );
      EarlyAdverseAppliedRiskFraction=(IsSmallProfitPosition(ticket)
         ? SmallEarlyAdverseRiskFraction : EarlyAdverseRiskFraction);
      if(EarlyAdverseInitialRiskUnits>0.0)
         EarlyAdverseTriggerLossUnits=EarlyAdverseInitialRiskUnits*EarlyAdverseAppliedRiskFraction;
      Print("Ramon EARLY ADVERSE init ticket=",ticket,
         " initial_risk=",DoubleToString(EarlyAdverseInitialRiskUnits,2),
         " trigger_loss=",DoubleToString(EarlyAdverseTriggerLossUnits,2),
         " fraction=",DoubleToString(EarlyAdverseAppliedRiskFraction,2));
   }

   if(EarlyAdverseInitialRiskUnits<=0.0 || EarlyAdverseTriggerLossUnits<=0.0)
      return false;

   datetime now=TimeCurrent();
   if(opened<=0 || now-opened<EarlyAdverseMinAgeSeconds)
      return false;

   double current_units=PositionGetDouble(POSITION_PROFIT);
   double loss_units=MathMax(0.0,-current_units);
   if(loss_units+0.00001<EarlyAdverseTriggerLossUnits)
   {
      EarlyAdverseWeakSnapshots=0;
      return false;
   }

   // Never interpret missing/stale model data as evidence that the trade is bad.
   if(LastModelSnapshotTime<=0 || LastModelSnapshotTime<opened
      || now-LastModelSnapshotTime>SnapshotIntervalSeconds*2+15
      || (LastModelDecision!="BUY" && LastModelDecision!="SELL" && LastModelDecision!="WAIT"))
   {
      EarlyAdverseWeakSnapshots=0;
      return false;
   }

   // Count at most once per distinct model snapshot; OnTimer itself runs every five seconds.
   if(EarlyAdverseLastDecisionTime!=LastModelSnapshotTime)
   {
      EarlyAdverseLastDecisionTime=LastModelSnapshotTime;
      long position_type=PositionGetInteger(POSITION_TYPE);
      string direction=(position_type==POSITION_TYPE_BUY ? "BUY" : "SELL");
      bool model_support=(LastModelDecision==direction);
      bool intrabar_support=(LastIntrabarConfirmed && LastIntrabarDirection==direction);
      bool trend_support=(LastAiTrendConfirmed && LastAiTrendDirection==direction);
      bool weak=(!model_support && !intrabar_support && !trend_support);

      if(weak)
         EarlyAdverseWeakSnapshots++;
      else
         EarlyAdverseWeakSnapshots=0;

      Print("Ramon EARLY ADVERSE check ticket=",ticket,
         " loss=",DoubleToString(loss_units,2),
         " trigger=",DoubleToString(EarlyAdverseTriggerLossUnits,2),
         " direction=",direction,
         " model=",LastModelDecision,
         " intrabar=",BoolText(intrabar_support),
         " trend=",BoolText(trend_support),
         " weak=",IntegerToString(EarlyAdverseWeakSnapshots),"/",
            IntegerToString(EarlyAdverseWeakSnapshotsRequired));
   }

   if(EarlyAdverseWeakSnapshots<EarlyAdverseWeakSnapshotsRequired)
      return false;

   EarlyAdverseTriggered=true;
   if(ManagedExitPausedForMarketClosed(ticket))
   {
      StatusLine="EARLY ADVERSE EXIT PAUSED: MARKET CLOSED";
      return true;
   }
   if(Trade.PositionClose(ticket,MaxDeviationPoints))
   {
      ResetMarketClosedExitPause();
      RecordDealTelemetry(Trade.ResultDeal(),"early_adverse_exit");
      StatusLine="EARLY ADVERSE EXIT loss="
         +DoubleToString(loss_units,2)
         +" trigger="+DoubleToString(EarlyAdverseTriggerLossUnits,2);
      Print("Ramon execution: ",StatusLine);
      return true;
   }

   HandleManagedExitFailure(ticket,"EARLY ADVERSE EXIT");
   return true;
}

void SelectDynamicProfitProtectionThresholds(const ulong ticket)
{
   ProfitProtectionInitialRiskUnits=ManagedPositionInitialRiskUnits(ticket);
   if(IsSmallProfitPosition(ticket))
   {
      ProfitProtectionActivationUnits=SmallProfitProtectionActivationUnits;
      ProfitProtectionGivebackUnits=SmallProfitProtectionGivebackUnits;
      return;
   }
   double activation=ProfitProtectionFallbackActivationUnits;
   if(ProfitProtectionInitialRiskUnits>0.0)
      activation=ProfitProtectionInitialRiskUnits*ProfitProtectionActivationRiskFraction;

   ProfitProtectionActivationUnits=MathMax(
      ProfitProtectionActivationMinUnits,
      MathMin(ProfitProtectionActivationMaxUnits,activation)
   );
   ProfitProtectionGivebackUnits=MathMax(
      ProfitProtectionGivebackMinUnits,
      MathMin(
         ProfitProtectionGivebackMaxUnits,
         ProfitProtectionActivationUnits*ProfitProtectionGivebackFraction
      )
   );
}

void ObserveOpenPositionProfit(const ulong ticket)
{
   if(!EnableProfitProtection || ticket==0 || !PositionSelectByTicket(ticket))
      return;

   if(ProfitProtectionTicket!=ticket)
   {
      ResetProfitProtectionState();
      ProfitProtectionTicket=ticket;
      SelectDynamicProfitProtectionThresholds(ticket);
      Print("Ramon PROFIT PROTECTION dynamic ticket=",ticket,
         " initial_risk=",DoubleToString(ProfitProtectionInitialRiskUnits,2),
         " activation=",DoubleToString(ProfitProtectionActivationUnits,2),
         " giveback=",DoubleToString(ProfitProtectionGivebackUnits,2));
   }

   ProfitProtectionCurrentUnits=PositionGetDouble(POSITION_PROFIT);
   if(ProfitProtectionCurrentUnits>ProfitProtectionPeakUnits)
      ProfitProtectionPeakUnits=ProfitProtectionCurrentUnits;

   ProfitProtectionArmed=(ProfitProtectionPeakUnits>=ProfitProtectionActivationUnits);
   ProfitProtectionGivebackNowUnits=MathMax(
      0.0,ProfitProtectionPeakUnits-ProfitProtectionCurrentUnits
   );

   if(ProfitProtectionArmed
      && !ProfitProtectionShadowTriggered
      && ProfitProtectionGivebackNowUnits>=ProfitProtectionGivebackUnits)
   {
      ProfitProtectionShadowTriggered=true;
      ProfitProtectionShadowTriggerTime=TimeCurrent();
      Print("Ramon PROFIT PROTECTION trigger ticket=",ticket,
         " current=",DoubleToString(ProfitProtectionCurrentUnits,2),
         " peak=",DoubleToString(ProfitProtectionPeakUnits,2),
         " giveback=",DoubleToString(ProfitProtectionGivebackNowUnits,2),
         " activation=",DoubleToString(ProfitProtectionActivationUnits,2),
         " threshold=",DoubleToString(ProfitProtectionGivebackUnits,2));
   }

   if(ObserveEarlyReversalExit
      && !EarlyReversalShadowTriggered
      && ProfitProtectionPeakUnits>=EarlyReversalMinPeakUnits
      && ProfitProtectionGivebackNowUnits>=EarlyReversalGivebackUnits
      && ProfitProtectionCurrentUnits<=EarlyReversalMaxCurrentUnits
      && !LastIntrabarConfirmed
      && !LastAiTrendConfirmed)
   {
      EarlyReversalShadowTriggered=true;
      EarlyReversalShadowTriggerTime=TimeCurrent();
      Print("Ramon EARLY REVERSAL SHADOW ticket=",ticket,
         " current=",DoubleToString(ProfitProtectionCurrentUnits,2),
         " peak=",DoubleToString(ProfitProtectionPeakUnits,2),
         " giveback=",DoubleToString(ProfitProtectionGivebackNowUnits,2),
         " intrabar=FAIL ai_trend=FAIL");
   }
}


bool IsRangeTradeComment(const string comment)
{
   return StringFind(comment,"Ramon:")==0 && StringSubstr(comment,22,2)==":R";
}

int RangeMainCooldownRemaining()
{
   datetime now=TimeCurrent();
   if(!HistorySelect(now-86400,now)) return -1;
   int total=HistoryDealsTotal();
   for(int i=total-1;i>=0;i--)
   {
      ulong close_deal=HistoryDealGetTicket(i);
      datetime closed=(datetime)HistoryDealGetInteger(close_deal,DEAL_TIME);
      if(now-closed>=300) break;
      if(HistoryDealGetString(close_deal,DEAL_SYMBOL)!=_Symbol
         || (ulong)HistoryDealGetInteger(close_deal,DEAL_MAGIC)!=MagicNumber
         || HistoryDealGetInteger(close_deal,DEAL_ENTRY)!=DEAL_ENTRY_OUT) continue;
      ulong identifier=(ulong)HistoryDealGetInteger(close_deal,DEAL_POSITION_ID);
      for(int j=i-1;j>=0;j--)
      {
         ulong entry_deal=HistoryDealGetTicket(j);
         if((ulong)HistoryDealGetInteger(entry_deal,DEAL_POSITION_ID)==identifier
            && HistoryDealGetInteger(entry_deal,DEAL_ENTRY)==DEAL_ENTRY_IN
            && IsRangeTradeComment(HistoryDealGetString(entry_deal,DEAL_COMMENT)))
            return (int)(300-(now-closed));
      }
   }
   return 0;
}

bool ManageRangeMainPosition(const ulong ticket,const datetime opened)
{
   if(!PositionSelectByTicket(ticket) || !IsRangeTradeComment(PositionGetString(POSITION_COMMENT)))
      return false;
   if(TimeCurrent()-opened<1800)
   { StatusLine="Managed RANGE MAIN OPEN | 5c TP / boundary SL <=5c / 30min"; return true; }
   if(ManagedExitPausedForMarketClosed(ticket))
   { StatusLine="RANGE EXIT PAUSED: MARKET CLOSED"; return true; }
   if(Trade.PositionClose(ticket,MaxDeviationPoints))
   {
      ResetMarketClosedExitPause();
      RecordDealTelemetry(Trade.ResultDeal(),"maximum_hold_bars");
      StatusLine="RANGE MAIN 30min TIME EXIT";
   }
   else HandleManagedExitFailure(ticket,"RANGE MAIN TIME EXIT");
   return true;
}

// Account-wide, entry-only limits. Existing positions keep their exit management.
// Peak tracks equity minus cash transfers, so deposits cannot erase trading loss
// and withdrawals do not create fictitious drawdown. Persistent across restarts.
bool AccountLossLimitsBlocked(string &reason)
{
   reason="";
   if(!EnableAccountLossLimits)
   { AccountLossLimitStatus="DISABLED"; return false; }
   if(!MathIsValidNumber(DailyLossLimitPercent) || !MathIsValidNumber(MaximumEquityDrawdownPercent)
      || DailyLossLimitPercent<0.0 || DailyLossLimitPercent>=100.0
      || MaximumEquityDrawdownPercent<0.0 || MaximumEquityDrawdownPercent>=100.0
      || (DailyLossLimitPercent==0.0 && MaximumEquityDrawdownPercent==0.0))
   { reason="ACCOUNT LOSS LIMITS: invalid/unconfigured"; AccountLossLimitStatus=reason; return true; }
   datetime now=TimeCurrent();
   datetime day=now-now%86400;
   double balance=AccountInfoDouble(ACCOUNT_BALANCE);
   double equity=AccountInfoDouble(ACCOUNT_EQUITY);
   if(!MathIsValidNumber(balance) || !MathIsValidNumber(equity)
      || !MathIsValidNumber(AccountInfoDouble(ACCOUNT_CREDIT))
      || balance<=0.0 || equity<=0.0 || !HistorySelect(0,now))
   { reason="ACCOUNT LOSS LIMITS: account/history unavailable"; AccountLossLimitStatus=reason; return true; }
   double cash=0.0,today_cash=0.0,today_net=0.0;
   for(int i=0;i<HistoryDealsTotal();i++)
   {
      ulong deal=HistoryDealGetTicket(i);
      if(deal==0)
      { reason="ACCOUNT LOSS LIMITS: incomplete history"; AccountLossLimitStatus=reason; return true; }
      long type=HistoryDealGetInteger(deal,DEAL_TYPE);
      double net=HistoryDealGetDouble(deal,DEAL_PROFIT)
         +HistoryDealGetDouble(deal,DEAL_COMMISSION)
         +HistoryDealGetDouble(deal,DEAL_SWAP)+HistoryDealGetDouble(deal,DEAL_FEE);
      if(!MathIsValidNumber(net))
      { reason="ACCOUNT LOSS LIMITS: invalid deal values"; AccountLossLimitStatus=reason; return true; }
      bool transfer=(type==DEAL_TYPE_BALANCE || type==DEAL_TYPE_CREDIT);
      bool today=((datetime)HistoryDealGetInteger(deal,DEAL_TIME)>=day);
      if(transfer) { cash+=net; if(today && type==DEAL_TYPE_BALANCE) today_cash+=net; }
      else if(today) today_net+=net;
   }
   string prefix="RamonRisk:"+IntegerToString(AccountInfoInteger(ACCOUNT_LOGIN))+":"
      +StringSubstr(AccountInfoString(ACCOUNT_SERVER),0,24)+":";
   string peak_key=prefix+"PEAK";
   string day_key=prefix+"DAY";
   double adjusted=equity-cash;
   if(!GlobalVariableCheck(peak_key) && GlobalVariableSet(peak_key,adjusted)==0)
   { reason="ACCOUNT LOSS LIMITS: peak persistence failed"; AccountLossLimitStatus=reason; return true; }
   double peak=GlobalVariableGet(peak_key);
   if(!MathIsValidNumber(peak))
   { reason="ACCOUNT LOSS LIMITS: invalid peak"; AccountLossLimitStatus=reason; return true; }
   if(adjusted>peak)
   {
      if(!GlobalVariableSetOnCondition(peak_key,adjusted,peak))
         peak=GlobalVariableGet(peak_key);
      else peak=adjusted;
   }
   double high_water=peak+cash;
   double start_balance=balance-today_net-today_cash;
   double floating=equity-balance-AccountInfoDouble(ACCOUNT_CREDIT);
   double day_loss=MathMax(0.0,-(today_net+floating));
   bool day_latched=(GlobalVariableCheck(day_key) && GlobalVariableGet(day_key)==(double)day);
   if(DailyLossLimitPercent>0.0 && (start_balance<=0.0 || day_latched
      || day_loss>=start_balance*DailyLossLimitPercent/100.0))
   {
      GlobalVariableSet(day_key,(double)day);
      GlobalVariablesFlush();
      reason="ACCOUNT LOSS LIMITS: daily loss entry lock"; AccountLossLimitStatus=reason; return true;
   }
   if(MaximumEquityDrawdownPercent>0.0 && (high_water<=0.0
      || high_water-equity>=high_water*MaximumEquityDrawdownPercent/100.0))
   { reason="ACCOUNT LOSS LIMITS: equity drawdown entry lock"; AccountLossLimitStatus=reason; return true; }
   GlobalVariablesFlush();
   AccountLossLimitStatus="READY";
   return false;
}

bool ManageMainMaximumHold(const ulong ticket,const datetime opened)
{
   int age=iBarShift(_Symbol,PERIOD_M15,opened,false);
   if(age<MaximumHoldBars) return false;
   if(ManagedExitPausedForMarketClosed(ticket))
   { StatusLine="EXIT PAUSED: MARKET CLOSED"; return true; }
   if(Trade.PositionClose(ticket,MaxDeviationPoints))
   {
      ResetMarketClosedExitPause();
      RecordDealTelemetry(Trade.ResultDeal(),"maximum_hold_bars");
      StatusLine="MAIN MAXIMUM HOLD EXIT";
   }
   else HandleManagedExitFailure(ticket,"MAIN MAXIMUM HOLD EXIT");
   return true;
}

void ManageOpenPosition()
{
   ulong ticket;
   datetime opened;
   if(!ManagedPosition(ticket,opened))
   {
      ResetProfitProtectionState();
      ResetTPStageRuntime();
      ResetEarlyAdverseState();
      ResetMainFastProfitState();
      ResetMarketClosedExitPause();
      return;
   }

   if(ManageNewsGuard(ticket)) return;
   if(ManageRangeMainPosition(ticket,opened)) return;

   // MAIN also closes materially losing positions after repeated loss of signal support.
   if(!SmallOnlyMode)
   {
      if(ManageTPStages(ticket))
         return;
      if(ManageEarlyAdverseExit(ticket,opened))
         return;
      if(ManageMainFastProfit(ticket,opened)) return;
      if(ManageMainMaximumHold(ticket,opened)) return;
      StatusLine="Managed MAIN OPEN | TP stages + EARLY ADVERSE + MAX HOLD";
      return;
   }

   if(EnforceSmallPositionRiskCap(ticket))
      return;
   if(ManageTPStages(ticket))
      return;
   ObserveOpenPositionProfit(ticket);
   if(ManageEarlyAdverseExit(ticket,opened))
      return;
   if(EnableProfitProtection && ProfitProtectionShadowTriggered)
   {
      if(ManagedExitPausedForMarketClosed(ticket))
      {
         StatusLine="EXIT PAUSED: MARKET CLOSED";
         return;
      }
      if(Trade.PositionClose(ticket,MaxDeviationPoints))
      {
         ResetMarketClosedExitPause();
         RecordDealTelemetry(Trade.ResultDeal(),"profit_protection");
         StatusLine="PROFIT PROTECTION EXIT peak="
            +DoubleToString(ProfitProtectionPeakUnits,2)
            +" giveback="+DoubleToString(ProfitProtectionGivebackNowUnits,2);
         Print("Ramon execution: ",StatusLine);
         return;
      }
      HandleManagedExitFailure(ticket,"PROFIT PROTECTION EXIT");
      return;
   }

   int age=iBarShift(_Symbol,PERIOD_M15,opened,false);
   if(age<SmallProfitMaximumHoldBars)
   {
      StatusLine="Managed SMALL position OPEN";
      return;
   }
   if(ManagedExitPausedForMarketClosed(ticket))
   {
      StatusLine="EXIT PAUSED: MARKET CLOSED";
      return;
   }
   if(Trade.PositionClose(ticket,MaxDeviationPoints))
   {
      ResetMarketClosedExitPause();
      RecordDealTelemetry(Trade.ResultDeal(),"maximum_hold_bars");
      StatusLine="SMALL TIME EXIT "+IntegerToString(age)+" bars";
   }
   else
      HandleManagedExitFailure(ticket,"SMALL TIME EXIT");
}

void OnTick()
{
   // Price crossings must not wait for the 5-second timer or 30-second model snapshot.
   // This handler only advances TP stages and tightens SL; it never creates entries.
   if(PositionSelect(_Symbol) && IsRangeTradeComment(PositionGetString(POSITION_COMMENT))) return;
   ObserveTPStageCrossingsOnTick();
}

void OnTimer()
{
   ShowStatus();
   if(!(bool)TerminalInfoInteger(TERMINAL_CONNECTED))
   { StatusLine="Terminal disconnected"; ShowStatus(); return; }
   ulong ticket;
   datetime opened;
   if(EnableAccountLossLimits)
   { string observed_loss_reason=""; AccountLossLimitsBlocked(observed_loss_reason); }
   bool has_managed=ManagedPosition(ticket,opened);
   if(has_managed) ManageOpenPosition(); // Exits always run before network telemetry.
   datetime closed=iTime(_Symbol,PERIOD_M15,1);
   if(closed<=0)
      return;

   datetime now=TimeCurrent();
   if(LastDecisionRequestTime>0
      && now-LastDecisionRequestTime<SnapshotIntervalSeconds)
   {
      // Never issue trade telemetry immediately before a live model decision.
      // On Wine/MT5, back-to-back WebRequest calls can fail locally with 1003/5203.
      // Use idle timer cycles for learning telemetry and always prioritize /decision.
      SyncClosedTrades();
      return;
   }
   LastDecisionRequestTime=now;

   string payload,reply;
   datetime bar_time=0;
   if(!BuildRequest(payload,bar_time) || !QueryModel(payload,reply))
   { LastModelSnapshotTime=0; EarlyAdverseWeakSnapshots=0; ShowStatus(); return; }
   string decision="",reason="",base_decision="",base_reason="",intrabar_direction="";
   double ensemble_ready=0.0,ensemble_active=0.0;
   string sample_key="",bundle_id="";
   double sample_saved=0.0;
   double regime_probability=-1.0,entry_probability=-1.0,news_probability=-1.0,meta_probability=-1.0;
   double risk_model_ready=0.0,risk_probability=-1.0,risk_multiplier=1.0;
   double news_source_ready=0.0,news_model_ready=0.0,news_source_age_seconds=-1.0;
   double news_event_time=0.0,news_event_delta_minutes=0.0;
   string news_source="",news_event_title="",news_event_country="",news_event_impact="";
   double signal_time=0.0,signal_bid=0.0,signal_ask=0.0,median=0.0,atr=0.0,stop_distance=0.0,target_distance=0.0;
   double target_learning_active=0.0,target_structure_ready=0.0;
   string target_method="",target_direction="";
   double target_impulse_start=0.0,target_impulse_end=0.0,target_impulse_range=0.0,target_impulse_atr=0.0;
   double target_tp1=0.0,target_tp2=0.0,target_tp3=0.0,legacy_target_price=0.0;
   double forecast_low=0.0,forecast_high=0.0,edge=0.0,model_spread=0.0;
   double buy_edge=0.0,sell_edge=0.0,minimum_edge=0.0,uncertainty=0.0,signal_strength=0.0,minimum_strength=0.0;
   double intrabar_confirmed=0.0,intrabar_move_atr=0.0,intrabar_rebound_atr=0.0;
   double intrabar_min_strength=0.0,intrabar_min_move_atr=0.0,intrabar_min_rebound_atr=0.0;
   string ai_trend_direction="";
   double ai_trend_confirmed=0.0,ai_trend_score=0.0,ai_trend_move_atr=0.0,ai_trend_consistency=0.0;
   double trend_min_path_atr=0.0,trend_min_consistency=0.0,trend_min_edge_fraction=0.0,trend_min_micro_move_atr=0.0;
   if(!JsonText(reply,"decision",decision)
      || !JsonText(reply,"reason",reason)
      || !JsonText(reply,"sample_key",sample_key)
      || !JsonNumber(reply,"sample_saved",sample_saved)
      || !JsonText(reply,"ensemble_bundle",bundle_id)
      || !ValidSampleKey(sample_key)
      || !JsonText(reply,"base_decision",base_decision)
      || !JsonText(reply,"base_reason",base_reason)
      || !JsonNumber(reply,"ensemble_ready",ensemble_ready)
      || !JsonNumber(reply,"ensemble_active",ensemble_active)
      || !JsonNumber(reply,"regime_probability",regime_probability)
      || !JsonNumber(reply,"entry_probability",entry_probability)
      || !JsonNumber(reply,"news_probability",news_probability)
      || !JsonNumber(reply,"meta_probability",meta_probability)
      || !JsonNumber(reply,"risk_model_ready",risk_model_ready)
      || !JsonNumber(reply,"risk_probability",risk_probability)
      || !JsonNumber(reply,"risk_multiplier",risk_multiplier)
      || !JsonText(reply,"news_source",news_source)
      || !JsonNumber(reply,"news_source_ready",news_source_ready)
      || !JsonNumber(reply,"news_model_ready",news_model_ready)
      || !JsonNumber(reply,"news_source_age_seconds",news_source_age_seconds)
      || !JsonText(reply,"news_event_title",news_event_title)
      || !JsonText(reply,"news_event_country",news_event_country)
      || !JsonText(reply,"news_event_impact",news_event_impact)
      || !JsonNumber(reply,"news_event_time",news_event_time)
      || !JsonNumber(reply,"news_event_delta_minutes",news_event_delta_minutes)
      || !JsonNumber(reply,"signal_bar_time",signal_time)
      || !JsonNumber(reply,"signal_bid",signal_bid)
      || !JsonNumber(reply,"signal_ask",signal_ask)
      || !JsonNumber(reply,"forecast_low",forecast_low)
      || !JsonNumber(reply,"forecast_median",median)
      || !JsonNumber(reply,"forecast_high",forecast_high)
      || !JsonNumber(reply,"atr",atr)
      || !JsonNumber(reply,"edge",edge)
      || !JsonNumber(reply,"buy_edge",buy_edge)
      || !JsonNumber(reply,"sell_edge",sell_edge)
      || !JsonNumber(reply,"minimum_edge",minimum_edge)
      || !JsonNumber(reply,"uncertainty",uncertainty)
      || !JsonNumber(reply,"signal_strength",signal_strength)
      || !JsonNumber(reply,"minimum_strength",minimum_strength)
      || !JsonNumber(reply,"intrabar_confirmed",intrabar_confirmed)
      || !JsonText(reply,"intrabar_direction",intrabar_direction)
      || !JsonNumber(reply,"intrabar_move_atr",intrabar_move_atr)
      || !JsonNumber(reply,"intrabar_rebound_atr",intrabar_rebound_atr)
      || !JsonNumber(reply,"intrabar_min_strength",intrabar_min_strength)
      || !JsonNumber(reply,"intrabar_min_move_atr",intrabar_min_move_atr)
      || !JsonNumber(reply,"intrabar_min_rebound_atr",intrabar_min_rebound_atr)
      || !JsonNumber(reply,"ai_trend_confirmed",ai_trend_confirmed)
      || !JsonText(reply,"ai_trend_direction",ai_trend_direction)
      || !JsonNumber(reply,"ai_trend_score",ai_trend_score)
      || !JsonNumber(reply,"ai_trend_move_atr",ai_trend_move_atr)
      || !JsonNumber(reply,"ai_trend_consistency",ai_trend_consistency)
      || !JsonNumber(reply,"trend_min_path_atr",trend_min_path_atr)
      || !JsonNumber(reply,"trend_min_consistency",trend_min_consistency)
      || !JsonNumber(reply,"trend_min_edge_fraction",trend_min_edge_fraction)
      || !JsonNumber(reply,"trend_min_micro_move_atr",trend_min_micro_move_atr)
      || !JsonNumber(reply,"spread_points",model_spread)
      || !JsonNumber(reply,"stop_distance",stop_distance)
      || !JsonNumber(reply,"target_distance",target_distance)
      || !JsonNumber(reply,"target_learning_active",target_learning_active)
      || !JsonNumber(reply,"target_structure_ready",target_structure_ready)
      || !JsonText(reply,"target_method",target_method)
      || !JsonText(reply,"target_direction",target_direction)
      || !JsonNumber(reply,"target_impulse_start",target_impulse_start)
      || !JsonNumber(reply,"target_impulse_end",target_impulse_end)
      || !JsonNumber(reply,"target_impulse_range",target_impulse_range)
      || !JsonNumber(reply,"target_impulse_atr",target_impulse_atr)
      || !JsonNumber(reply,"target_tp1",target_tp1)
      || !JsonNumber(reply,"target_tp2",target_tp2)
      || !JsonNumber(reply,"target_tp3",target_tp3)
      || !JsonNumber(reply,"legacy_target_price",legacy_target_price)
      || risk_model_ready<0.0 || risk_model_ready>1.0
      || risk_probability<-1.0 || risk_probability>1.0
      || risk_multiplier<0.50 || risk_multiplier>1.50
      || (risk_model_ready>=0.5 && risk_probability<0.0)
      || (risk_model_ready<0.5 && (MathAbs(risk_multiplier-1.0)>0.000001 || risk_probability!=-1.0))
      || (datetime)signal_time!=bar_time
      || (decision!="BUY" && decision!="SELL" && decision!="WAIT"))
   { LastModelSnapshotTime=0; EarlyAdverseWeakSnapshots=0; StatusLine="Invalid/stale model response"; ShowStatus(); return; }
   LastModelSnapshotTime=LastDecisionRequestTime;
   LastSampleKey=sample_key;
   LastSampleSaved=(sample_saved>=0.5);
   LastBundleId=bundle_id;
   LastModelDecision=decision;
   LastModelReason=reason;
   LastBaseDecision=base_decision;
   LastBaseReason=base_reason;
   LastEnsembleReady=(ensemble_ready>=0.5);
   LastEnsembleActive=(ensemble_active>=0.5);
   LastRegimeProbability=regime_probability;
   LastEntryProbability=entry_probability;
   LastNewsProbability=news_probability;
   LastMetaProbability=meta_probability;
   LastRiskModelReady=(risk_model_ready>=0.5);
   LastRiskProbability=risk_probability;
   LastRiskMultiplier=risk_multiplier;
   // Optional telemetry only: these values are never read by entry/exit/sizing.
   double role_shadow=0.0,shadow_risk_probability=-1.0;
   string shadow_regime_label="UNAVAILABLE";
   JsonNumber(reply,"role_shadow",role_shadow);
   JsonNumber(reply,"shadow_risk_probability",shadow_risk_probability);
   JsonText(reply,"shadow_regime_label",shadow_regime_label);
   LastRoleShadow=(role_shadow>=0.5);
   LastShadowRegimeLabel=shadow_regime_label;
   LastShadowRiskProbability=shadow_risk_probability;
   LastNewsSource=news_source;
   LastNewsSourceReady=(news_source_ready>=0.5);
   LastNewsModelReady=(news_model_ready>=0.5);
   // Optional model identity telemetry. Older servers remain compatible.
   JsonText(reply,"forecast_model_handler",LastForecastModelHandler);
   JsonText(reply,"forecast_shadow_model_handler",LastForecastShadowModelHandler);
   JsonText(reply,"regime_model_handler",LastRegimeModelHandler);
   JsonText(reply,"entry_model_handler",LastEntryModelHandler);
   JsonText(reply,"news_model_handler",LastNewsModelHandler);
   JsonText(reply,"meta_model_handler",LastMetaModelHandler);
   JsonText(reply,"risk_model_handler",LastRiskModelHandler);
   JsonText(reply,"news_source_handler",LastNewsSourceHandler);
   JsonText(reply,"market_state_handler",LastMarketStateHandler);
   JsonText(reply,"target_model_handler",LastTargetModelHandler);
   JsonText(reply,"anomaly_model_handler",LastAnomalyModelHandler);
   JsonText(reply,"news_sentiment_model_handler",LastNewsSentimentModelHandler);

   // Optional live snapshot telemetry used only by dashboard tooltips.
   double timesfm_ready=0.0,timesfm_low=-1.0,timesfm_median=-1.0,timesfm_high=-1.0,timesfm_move_atr=-1.0,timesfm_agrees=0.0;
   string timesfm_direction="UNAVAILABLE";
   JsonNumber(reply,"timesfm3_shadow_ready",timesfm_ready);
   JsonText(reply,"timesfm3_shadow_direction",timesfm_direction);
   JsonNumber(reply,"timesfm3_shadow_low",timesfm_low);
   JsonNumber(reply,"timesfm3_shadow_median",timesfm_median);
   JsonNumber(reply,"timesfm3_shadow_high",timesfm_high);
   JsonNumber(reply,"timesfm3_shadow_move_atr",timesfm_move_atr);
   JsonNumber(reply,"timesfm3_shadow_agrees_chronos",timesfm_agrees);
   LastTimesFMReady=(timesfm_ready>=0.5);
   LastTimesFMDirection=timesfm_direction;
   LastTimesFMLow=timesfm_low;
   LastTimesFMMedian=timesfm_median;
   LastTimesFMHigh=timesfm_high;
   LastTimesFMMoveAtr=timesfm_move_atr;
   LastTimesFMAgreesChronos=(timesfm_agrees>=0.5);

   JsonText(reply,"market_state",LastMarketState);
   JsonText(reply,"market_state_route",LastMarketStateRoute);
   JsonText(reply,"market_state_policy",LastMarketStatePolicy);
   JsonText(reply,"risk_target",LastRiskTarget);

   double moment_ready=0.0,moment_score=-1.0,moment_ratio=-1.0;
   double finbert_ready=0.0,finbert_directional=0.0;
   string moment_label="UNAVAILABLE",finbert_label="UNAVAILABLE";
   JsonNumber(reply,"moment_shadow_ready",moment_ready);
   JsonNumber(reply,"moment_anomaly_score",moment_score);
   JsonNumber(reply,"moment_anomaly_ratio",moment_ratio);
   JsonText(reply,"moment_anomaly_label",moment_label);
   JsonNumber(reply,"finbert_shadow_ready",finbert_ready);
   JsonText(reply,"finbert_sentiment_label",finbert_label);
   JsonNumber(reply,"finbert_directional_score",finbert_directional);
   LastMomentShadowReady=(moment_ready>=0.5);
   LastMomentAnomalyScore=moment_score;
   LastMomentAnomalyRatio=moment_ratio;
   LastMomentAnomalyLabel=moment_label;
   LastFinbertShadowReady=(finbert_ready>=0.5);
   LastFinbertSentimentLabel=finbert_label;
   LastFinbertDirectionalScore=finbert_directional;
   double moment_live_active=0.0,moment_live_fresh=0.0,moment_live_veto=0.0,moment_live_threshold=2.0;
   double finbert_live_active=0.0,finbert_live_veto=0.0,finbert_live_threshold=0.35;
   JsonNumber(reply,"moment_live_active",moment_live_active);
   JsonNumber(reply,"moment_live_fresh",moment_live_fresh);
   JsonNumber(reply,"moment_live_veto",moment_live_veto);
   JsonNumber(reply,"moment_live_threshold",moment_live_threshold);
   JsonNumber(reply,"finbert_live_active",finbert_live_active);
   JsonNumber(reply,"finbert_live_veto",finbert_live_veto);
   JsonNumber(reply,"finbert_live_threshold",finbert_live_threshold);
   LastMomentLiveActive=(moment_live_active>=0.5);
   LastMomentLiveFresh=(moment_live_fresh>=0.5);
   LastMomentLiveVeto=(moment_live_veto>=0.5);
   LastMomentLiveThreshold=moment_live_threshold;
   LastFinbertLiveActive=(finbert_live_active>=0.5);
   LastFinbertLiveVeto=(finbert_live_veto>=0.5);
   LastFinbertLiveThreshold=finbert_live_threshold;
   LastNewsSourceAgeSeconds=news_source_age_seconds;
   LastNewsEventTitle=news_event_title;
   LastNewsEventCountry=news_event_country;
   LastNewsEventImpact=news_event_impact;
   LastNewsEventTime=(datetime)news_event_time;
   double high_event_utc=0.0;
   if(JsonNumber(reply,"news_high_event_time",high_event_utc))
      NewsHighEventUTC=(datetime)high_event_utc;
   else
      NewsHighEventUTC=(LastNewsEventImpact=="High" && LastNewsEventCountry=="USD" ? LastNewsEventTime : 0);
   NewsGuardReceivedUTC=TimeGMT();
   LastNewsEventDeltaMinutes=news_event_delta_minutes;
   LastSignalBarTime=bar_time;
   LastSignalBid=signal_bid;
   LastSignalAsk=signal_ask;
   LastForecastLow=forecast_low;
   LastForecast=median;
   LastForecastHigh=forecast_high;
   LastAtr=atr;
   LastEdge=edge;
   LastBuyEdge=buy_edge;
   LastSellEdge=sell_edge;
   LastMinimumEdge=minimum_edge;
   LastUncertainty=uncertainty;
   LastSignalStrength=signal_strength;
   LastMinimumStrength=minimum_strength;
   LastIntrabarConfirmed=(intrabar_confirmed>=0.5);
   LastIntrabarDirection=intrabar_direction;
   LastIntrabarMoveAtr=intrabar_move_atr;
   LastIntrabarReboundAtr=intrabar_rebound_atr;
   LastIntrabarMinStrength=intrabar_min_strength;
   LastIntrabarMinMoveAtr=intrabar_min_move_atr;
   LastIntrabarMinReboundAtr=intrabar_min_rebound_atr;
   LastAiTrendConfirmed=(ai_trend_confirmed>=0.5);
   LastAiTrendDirection=ai_trend_direction;
   LastAiTrendScore=ai_trend_score;
   LastAiTrendMoveAtr=ai_trend_move_atr;
   LastAiTrendConsistency=ai_trend_consistency;
   LastTrendMinPathAtr=trend_min_path_atr;
   LastTrendMinConsistency=trend_min_consistency;
   LastTrendMinEdgeFraction=trend_min_edge_fraction;
   LastTrendMinMicroMoveAtr=trend_min_micro_move_atr;
   LastModelSpreadPoints=(int)model_spread;
   LastStopDistance=stop_distance;
   LastTargetDistance=target_distance;
   LastTargetLearningActive=(target_learning_active>=0.5);
   LastTargetStructureReady=(target_structure_ready>=0.5);
   LastTargetMethod=target_method;
   LastTargetDirection=target_direction;
   LastTargetImpulseStart=target_impulse_start;
   LastTargetImpulseEnd=target_impulse_end;
   LastTargetImpulseRange=target_impulse_range;
   LastTargetImpulseAtr=target_impulse_atr;
   LastTargetTP1=target_tp1;
   LastTargetTP2=target_tp2;
   LastTargetTP3=target_tp3;
   LastLegacyTargetPrice=legacy_target_price;
   StatusLine=reason;
   UpdateSizingPreview();
   UpdateImprovementShadows();
   AppendSignalCsv();
   AppendImprovementShadowCsv();
   Print("Ramon ",UTCText(bar_time,TIME_DATE|TIME_SECONDS)," ",decision," ",reason,
      " median=",DoubleToString(median,_Digits));

   if(ManagedPosition(ticket,opened) && ManageNewsGuard(ticket))
   { ShowStatus(); return; }
   if(NewsGuardEntryBlocked())
   { StatusLine="NEWS GUARD: entry paused (high-impact window or calendar unavailable)"; ShowStatus(); return; }

   // Learning snapshots continue while positions exist; execution remains single-position.
   if(ManagedPosition(ticket,opened))
   { StatusLine=(LastSampleSaved ? "Managed position OPEN; learning snapshot saved" : "Managed position OPEN; snapshot storage failed"); ShowStatus(); return; }
   if(OtherPositionOnSymbol())
   { StatusLine="Another robot has a position on this symbol"; ShowStatus(); return; }
   if(decision!="BUY" && decision!="SELL" && decision!="WAIT")
   { StatusLine="Unknown model decision"; ShowStatus(); return; }
   if(SmallOnlyMode && decision!="WAIT")
   { StatusLine="Primary signal; small EA stands aside"; ShowStatus(); return; }
   string small_direction="",small_filter_reason="";
   bool small_profit=SmallProfitCandidate(decision,reason,buy_edge,sell_edge,
      signal_strength,small_direction,small_filter_reason);
   if(decision=="WAIT" && !small_profit)
   {
      if(SmallOnlyMode && StringFind(small_filter_reason,"SMALL_FILTER_")==0)
         StatusLine=small_filter_reason;
      ShowStatus();
      return;
   }
   if(!EnableLiveTrading)
   { ShowStatus(); return; }
   if(small_profit)
      decision=small_direction;
   int small_entries_on_bar=0;
   if(SmallOnlyMode)
   {
      small_entries_on_bar=SmallEntriesThisSignalBar(bar_time);
      if(small_entries_on_bar<0)
      { StatusLine="Small entry history unavailable"; ShowStatus(); return; }
      if(small_entries_on_bar>0)
      {
         int small_loss_on_bar=SmallLossClosedThisSignalBar(bar_time);
         if(small_loss_on_bar<0)
         { StatusLine="Small loss-gate history unavailable"; ShowStatus(); return; }
         if(small_loss_on_bar>0)
         { StatusLine="Second SMALL blocked: first attempt lost this M15 bar"; ShowStatus(); return; }
      }
      if(small_entries_on_bar>=SmallProfitMaxEntriesPerSignalBar)
      { StatusLine="Two small entries already used for this M15 signal bar"; ShowStatus(); return; }
   }
   else if(LastEntrySignalBar==bar_time)
   { StatusLine="Entry already used for this M15 signal bar"; ShowStatus(); return; }
   string live_block_reason="";
   if(!LiveExecutionReady(live_block_reason))
   { StatusLine=live_block_reason; ShowStatus(); return; }
   if(!(bool)TerminalInfoInteger(TERMINAL_TRADE_ALLOWED)
      || !(bool)MQLInfoInteger(MQL_TRADE_ALLOWED)
      || !(bool)AccountInfoInteger(ACCOUNT_TRADE_ALLOWED))
   { StatusLine="Trade permission denied"; ShowStatus(); return; }
   if(SymbolInfoInteger(_Symbol,SYMBOL_TRADE_MODE)!=SYMBOL_TRADE_MODE_FULL)
   { StatusLine="Symbol trading disabled"; ShowStatus(); return; }
   string account_loss_reason="";
   if(AccountLossLimitsBlocked(account_loss_reason))
   { StatusLine=account_loss_reason; ShowStatus(); return; }
   string cooldown_reason="";
   if(LocalLossCooldownBlocked(decision,cooldown_reason))
   { StatusLine=cooldown_reason; ShowStatus(); return; }
   int today=(SmallOnlyMode ? 0 : TradesToday());
   if(!SmallOnlyMode && (today<0 || today>=MaxTradesPerDay))
   { StatusLine="Daily trade limit/history unavailable"; ShowStatus(); return; }
   MqlTick tick;
   if(!SymbolInfoTick(_Symbol,tick) || TimeCurrent()-tick.time>30
      || (int)SymbolInfoInteger(_Symbol,SYMBOL_SPREAD)>MaxSpreadPoints)
   { StatusLine="Quote changed/stale"; ShowStatus(); return; }
   if(stop_distance<=0.0 || target_distance<=0.0 || atr<=0.0)
   { StatusLine="Invalid stop/target"; ShowStatus(); return; }
   double range_execution=0.0,range_stop=0.0,range_target=0.0,range_low=0.0,range_high=0.0;
   JsonNumber(reply,"range_execution",range_execution);
   bool range_trade=(range_execution>=0.5);
   if(!range_trade && (intrabar_confirmed<0.5 || ai_trend_confirmed<0.5
      || intrabar_direction!=decision || ai_trend_direction!=decision))
   { StatusLine="DIRECTION GUARD: normal entry needs aligned confirmations"; ShowStatus(); return; }

   if(range_trade)
   {
      if(!EnableRangeMain || SmallOnlyMode || small_profit
         || (reason!="range_reversal_buy" && reason!="range_reversal_sell")
         || !JsonNumber(reply,"range_stop_price",range_stop)
         || !JsonNumber(reply,"range_target_price",range_target)
         || !JsonNumber(reply,"range_low",range_low)
         || !JsonNumber(reply,"range_high",range_high))
      { StatusLine="Range MAIN protocol not ready"; ShowStatus(); return; }
      int cooldown=RangeMainCooldownRemaining();
      if(cooldown!=0)
      { StatusLine="Range MAIN cooldown/history unavailable"; ShowStatus(); return; }
   }
   ENUM_ORDER_TYPE side=(decision=="BUY" ? ORDER_TYPE_BUY : ORDER_TYPE_SELL);
   double entry=(decision=="BUY" ? tick.ask : tick.bid);
   double point=SymbolInfoDouble(_Symbol,SYMBOL_POINT);
   double min_stop=(double)SymbolInfoInteger(_Symbol,SYMBOL_TRADE_STOPS_LEVEL)*point;
   stop_distance=MathMax(stop_distance,min_stop+2*point);
   target_distance=MathMax(target_distance,min_stop+2*point);
   double stop=NormalizeDouble(entry+(decision=="BUY" ? -stop_distance : stop_distance),_Digits);
   double target=NormalizeDouble(entry+(decision=="BUY" ? target_distance : -target_distance),_Digits);

   if(range_trade)
   {
      stop=NormalizeDouble(range_stop,_Digits);
      target=NormalizeDouble(range_target,_Digits);
      double risk=(decision=="BUY" ? entry-stop : stop-entry);
      double reward=(decision=="BUY" ? target-entry : entry-target);
      double spread=tick.ask-tick.bid;
      bool boundary=(decision=="BUY" ? tick.bid<=range_low+0.2*(range_high-range_low)
                                     : tick.bid>=range_high-0.2*(range_high-range_low));
      if(tick.bid<range_low || tick.bid>range_high || !boundary
         || risk<min_stop+2*point || reward<min_stop+2*point
         || reward<MathMax(3*spread,1.2*risk))
      { StatusLine="Range MAIN quote/risk changed"; ShowStatus(); return; }
   }

   // MAIN three-stage plan: use TP3 as the broker-side fail-safe target whenever
   // the model supplied a valid directional TP1/TP2/TP3 structure.
   bool main_tp_plan_valid=(
      !small_profit && !range_trade
      && EnableTPStageManagement
      && ValidDirectionalTargets(decision,entry,LastTargetTP1,LastTargetTP2,LastTargetTP3)
   );
   if(main_tp_plan_valid)
   {
      double broker_tp3=NormalizeDouble(LastTargetTP3,_Digits);
      if(MathAbs(broker_tp3-entry)>=min_stop+2*point)
         target=broker_tp3;
   }

   double volume=(small_profit ? SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_MIN)
      : SelectVolume(side,entry,stop));
   if(volume<=0.0)
   { StatusLine="TRADE BLOCKED: min lot > hard risk cap"; ShowStatus(); return; }

   if(range_trade)
   {
      double range_loss_units=0.0;
      if(!OrderCalcProfit(side,_Symbol,volume,entry,stop,range_loss_units)
         || range_loss_units>=0.0
         || -range_loss_units>RangeMainMaxLossUnits+0.00001)
      {
         StatusLine="Range MAIN blocked: boundary SL risk > 5c";
         ShowStatus();
         return;
      }
      if(!RangeMainProfitTarget(side,entry,volume,tick,range_target,target))
      {
         StatusLine="Range MAIN blocked: 5c TP not inside midpoint";
         ShowStatus();
         return;
      }
   }

   if(range_trade && !RangeMainRewardRiskValid(side,entry,volume,stop,target))
   { StatusLine="Range MAIN blocked: actual quick TP reward/risk <1.2"; ShowStatus(); return; }

   if(small_profit)
   {
      // Small-profit trades never scale above the broker minimum volume.
      if(!SmallProfitStop(side,entry,stop,volume,tick,stop))
      { StatusLine="Broker cannot place 4-cent small stop"; ShowStatus(); return; }
      double stop_loss_units=0.0;
      double small_risk_cap=SmallProfitRiskCapUnits();
      if(volume<=0.0 || small_risk_cap<=0.0
         || !OrderCalcProfit(side,_Symbol,volume,entry,stop,stop_loss_units)
         || stop_loss_units>=0.0
         || -stop_loss_units>small_risk_cap+0.00001)
      { StatusLine="Small profit risk > 4 cents"; ShowStatus(); return; }
      if(!SmallProfitTarget(side,entry,volume,tick,target))
      { StatusLine="Broker cannot place 2-cent target"; ShowStatus(); return; }
   }
   double margin=0.0;
   if(!OrderCalcMargin(side,_Symbol,volume,entry,margin)
      || margin>AccountInfoDouble(ACCOUNT_MARGIN_FREE)*0.8)
   { StatusLine="Insufficient margin"; ShowStatus(); return; }

   // Telemetry/management staging must never block an otherwise valid entry.
   StageEntrySizing(LastSampleKey,side,entry,stop,volume);
   if(!small_profit && !range_trade)
      PersistTPPlan(LastSampleKey,decision,entry,stop,
         LastTargetTP1,LastTargetTP2,LastTargetTP3);
   // The broker owns SL/TP immediately. No position is opened when the model is unavailable.
   string trade_comment="Ramon:"+LastSampleKey+(small_profit ? ":S" : (range_trade ? ":R" : ""));
   bool submitted=(
      decision=="BUY"
      ? Trade.Buy(volume,_Symbol,0.0,stop,target,trade_comment)
      : Trade.Sell(volume,_Symbol,0.0,stop,target,trade_comment)
   );
   uint retcode=Trade.ResultRetcode();
   if(!submitted || (retcode!=TRADE_RETCODE_DONE && retcode!=TRADE_RETCODE_PLACED))
   {
      ClearPendingSizing();
      StatusLine="Order rejected "+IntegerToString((int)retcode);
   }
   else
   {
      LastEntrySignalBar=bar_time;
      if(SmallOnlyMode)
         LastSmallEntriesOnSignalBar=small_entries_on_bar+1;
      StatusLine="Order sent "+(small_profit ? "SMALL 2c " : "")
         +decision+" "+DoubleToString(volume,2);
   }
   Print("Ramon execution: ",StatusLine);
   ShowStatus();
}


void OnTradeTransaction(
   const MqlTradeTransaction &trans,
   const MqlTradeRequest &request,
   const MqlTradeResult &result
)
{
   if(trans.type==TRADE_TRANSACTION_DEAL_ADD && trans.deal>0)
   {
      RecordDealTelemetry(trans.deal);
      AppendTradeCsv(trans.deal);
   }
}

bool CloseManagedPositionFromDashboard()
{
   if(!AccountLockHealthy())
   {
      LastCloseStatus="FAILED: account lock";
      StatusLine="MANUAL CLOSE BLOCKED: account/server lock mismatch";
      Print("Ramon execution: ",StatusLine);
      return false;
   }

   bool permissions=(bool)TerminalInfoInteger(TERMINAL_TRADE_ALLOWED)
      && (bool)MQLInfoInteger(MQL_TRADE_ALLOWED)
      && (bool)AccountInfoInteger(ACCOUNT_TRADE_ALLOWED);
   if(!permissions)
   {
      LastCloseStatus="FAILED: permissions";
      StatusLine="MANUAL CLOSE BLOCKED: trading permissions";
      Print("Ramon execution: ",StatusLine);
      return false;
   }

   ulong ticket=0;
   datetime opened=0;
   if(!ManagedPosition(ticket,opened))
   {
      LastCloseStatus="NO RAMON POSITION";
      StatusLine="Manual close: no Ramon position";
      return false;
   }

   if(!PositionSelectByTicket(ticket))
   {
      LastCloseStatus="FAILED: position gone";
      StatusLine="Manual close failed: position unavailable";
      return false;
   }

   double profit_units=PositionGetDouble(POSITION_PROFIT);
   if(Trade.PositionClose(ticket,MaxDeviationPoints))
   {
      RecordDealTelemetry(Trade.ResultDeal(),"manual_dashboard_close");
      LastCloseStatus="CLOSED #"+IntegerToString((long)ticket);
      StatusLine="MANUAL CLOSE #"+IntegerToString((long)ticket)
         +" P/L "+DoubleToString(profit_units,2)+" units";
      Print("Ramon execution: ",StatusLine);
      return true;
   }

   uint retcode=Trade.ResultRetcode();
   LastCloseStatus="FAILED "+IntegerToString((int)retcode);
   StatusLine="MANUAL CLOSE FAILED "+IntegerToString((int)retcode);
   Print("Ramon execution: ",StatusLine);
   return false;
}

void OnChartEvent(const int id,const long &lparam,const double &dparam,const string &sparam)
{
   if(id!=CHARTEVENT_OBJECT_CLICK)
      return;

   if(sparam==UiPrefix+"COPY")
   {
      ObjectSetInteger(0,sparam,OBJPROP_STATE,false);
      CopyDiagnosticToClipboard();
      DrawDashboard();
      return;
   }

   if(sparam==UiPrefix+"CLOSE")
   {
      ObjectSetInteger(0,sparam,OBJPROP_STATE,false);
      CloseManagedPositionFromDashboard();
      DrawDashboard();
   }
}

int OnInit()
{
   if(_Symbol!=TradeSymbol || _Period!=PERIOD_M15 || StringFind(_Symbol,"XAUUSD")!=0)
   { Print("Attach only to ",TradeSymbol," M15"); return INIT_FAILED; }
   if(SmallOnlyMode && (MagicNumber!=SmallProfitMagicNumber
      || AccountInfoInteger(ACCOUNT_MARGIN_MODE)!=ACCOUNT_MARGIN_MODE_RETAIL_HEDGING))
   { Print("Small mode requires magic 26092213 and a hedging account"); return INIT_FAILED; }
   if(!SmallOnlyMode && MagicNumber==SmallProfitMagicNumber)
   { Print("Primary mode cannot use the small-trade magic number"); return INIT_FAILED; }
   if(MoneyUnitsPerUSD<=0.0 || RiskPerTradeUSD<=0.0 || RiskPerTradeUSD>0.50
      || MaxExecutableRiskUSD<=0.0 || MaxExecutableRiskUSD>0.50
      || MaxExecutableRiskUSD<EffectiveRiskPerTradeUSD()
      || MaxSpreadPoints<=0 || (!SmallOnlyMode && MaxTradesPerDay<1) || MaximumHoldBars<1
      || ProfitProtectionFallbackActivationUnits<=0.0
      || ProfitProtectionActivationMinUnits<=0.0
      || ProfitProtectionActivationMaxUnits<ProfitProtectionActivationMinUnits
      || ProfitProtectionActivationRiskFraction<=0.0
      || ProfitProtectionGivebackMinUnits<=0.0
      || ProfitProtectionGivebackMaxUnits<ProfitProtectionGivebackMinUnits
      || ProfitProtectionGivebackFraction<=0.0
      || ProfitProtectionGivebackMinUnits>=ProfitProtectionActivationMinUnits
      || EarlyProfitLockActivation1R<=0.0
      || EarlyProfitLockActivation2R<=EarlyProfitLockActivation1R
      || EarlyProfitLockStage1R<=0.0
      || EarlyProfitLockStage2R<=EarlyProfitLockStage1R
      || EarlyProfitLockStage1R>=EarlyProfitLockActivation1R
      || EarlyProfitLockStage2R>=EarlyProfitLockActivation2R
      || TPStageWeakSnapshotsRequired<1 || TP1GraceSeconds<0 || TP2GraceSeconds<0
      || MainFastProfitMinAgeBars<1 || MainFastProfitMinProfitUnits<=0.0
      || MainFastProfitMinProgressToTP1<=0.0 || MainFastProfitMinProgressToTP1>=1.0
      || MainFastProfitWeakSnapshotsRequired<1
      || TP1HealthyProgressFraction<=0.0 || TP1HealthyProgressFraction>=1.0
      || TP1RetraceFraction<=0.0 || TP1RetraceFraction>=1.0
      || TP2RetraceFraction<=0.0 || TP2RetraceFraction>=1.0
      || EarlyReversalMinPeakUnits<=0.0 || EarlyReversalGivebackUnits<=0.0
      || EarlyReversalMaxCurrentUnits>0.0
      || EarlyAdverseRiskFraction<=0.0 || EarlyAdverseRiskFraction>=1.0
      || SmallEarlyAdverseRiskFraction<=0.0 || SmallEarlyAdverseRiskFraction>=1.0
      || EarlyAdverseWeakSnapshotsRequired<1 || EarlyAdverseMinAgeSeconds<0
      || SnapshotIntervalSeconds<10
      || (WriteDiagnosticFile && StringLen(DiagnosticFileName)==0)
      || (WriteCsvLogs && (StringLen(SignalCsvFileName)==0 || StringLen(TradeCsvFileName)==0))
      || (EnableImprovementShadowPack && WriteCsvLogs && StringLen(ShadowCsvFileName)==0)
      || !IsAllowedModelUrl(ModelUrl))
   { Print("Invalid risk or local server settings"); return INIT_FAILED; }
   long current_login=AccountInfoInteger(ACCOUNT_LOGIN);
   string current_server=AccountInfoString(ACCOUNT_SERVER);
   if(current_login<=0 || StringLen(current_server)==0)
   { Print("Account identity unavailable"); return INIT_FAILED; }
   if(StringLen(RequiredServerText)>0 && StringFind(current_server,RequiredServerText)<0)
   { Print("Broker server mismatch"); return INIT_FAILED; }
   if(AutoLockCurrentAccount)
   {
      LockedAccountLogin=current_login;
      LockedAccountServer=current_server;
   }
   else
   {
      if(AllowedAccountLogin<=0 || current_login!=AllowedAccountLogin)
      { Print("Explicit account login mismatch"); return INIT_FAILED; }
      LockedAccountLogin=AllowedAccountLogin;
      LockedAccountServer=current_server;
   }
   if(AccountIsCent && MathAbs(MoneyUnitsPerUSD-100.0)>0.00001)
      Print("Ramon warning: CENT mode is configured but MoneyUnitsPerUSD is ",
         DoubleToString(MoneyUnitsPerUSD,4)," instead of 100");
   if(StringLen(ExpectedAccountCurrency)>0
      && AccountInfoString(ACCOUNT_CURRENCY)!=ExpectedAccountCurrency)
      Print("Ramon live BLOCKED: account currency mismatch: actual=",
         AccountInfoString(ACCOUNT_CURRENCY)," expected=",ExpectedAccountCurrency);
   if(EnableLiveTrading && !ConfirmMoneyUnitsPerUSD)
      Print("Ramon live BLOCKED: ConfirmMoneyUnitsPerUSD is false");
   if(EnableLiveTrading && !AccountLockHealthy())
      Print("Ramon live BLOCKED: account/server lock mismatch");
   Trade.SetExpertMagicNumber(MagicNumber);
   Trade.SetDeviationInPoints(MaxDeviationPoints);
   Trade.SetTypeFillingBySymbol(_Symbol);
   EventSetTimer(5);
   // Stagger the second chart's WebRequest cadence from the primary chart.
   if(SmallOnlyMode)
      LastDecisionRequestTime=TimeCurrent()-SnapshotIntervalSeconds+15;
   ObjectsDeleteAll(0,UiPrefix);
   ObjectsDeleteAll(0,TpUiPrefix);
   ShowStatus();
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   EventKillTimer();
   ObjectsDeleteAll(0,UiPrefix);
   ObjectsDeleteAll(0,TpUiPrefix);
   Comment("");
}
