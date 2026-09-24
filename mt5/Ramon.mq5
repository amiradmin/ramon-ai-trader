#property strict
#property version "0.30"
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
input bool EnableLiveTrading = false;
input string ModelUrl = "http://127.0.0.1:8012/decision";
input double MoneyUnitsPerUSD = 100.0; // CENT account: 100 account units = 1 USD.
input bool AccountIsCent = true; // Configured account mode; MT5 ACCOUNT_CURRENCY alone cannot identify CENT.
input bool ConfirmMoneyUnitsPerUSD = false; // Must be true before live trading can arm.
input string ExpectedAccountCurrency = ""; // Optional exact ACCOUNT_CURRENCY check when non-empty.
input double RiskPerTradeUSD = 0.06; // Preferred sizing budget.
input bool AllowMinLotRiskOverride = true; // Permit broker minimum lot above preferred budget.
input double MaxExecutableRiskUSD = 0.20; // Hard planned-risk cap for minimum-lot override.
input int MaxSpreadPoints = 50;
input int MaxTradesPerDay = 400;
input int MaximumHoldBars = 4;
input int RequestTimeoutMs = 4000;
input int SnapshotIntervalSeconds = 30; // Re-evaluate fresh Bid/Ask inside the same M15 bar.
input int MaxDeviationPoints = 30;
input ulong MagicNumber = 26092212;
input bool WriteDiagnosticFile = true;
input string DiagnosticFileName = "Ramon_Diagnostic.txt";
input bool ShowDashboard = true;
input bool EnableClipboardButton = true;
input bool WriteCsvLogs = true;
input string SignalCsvFileName = "Ramon_Signals.csv";
input string TradeCsvFileName = "Ramon_Trades.csv";

CTrade Trade;
datetime LastDecisionRequestTime = 0;
datetime LastEntrySignalBar = 0;
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
double LastEntryProbability = -1.0;
double LastNewsProbability = -1.0;
double LastMetaProbability = -1.0;
bool LastRiskModelReady = false;
double LastRiskProbability = -1.0;
double LastRiskMultiplier = 1.0;
bool LastNewsSourceReady = false;
bool LastNewsModelReady = false;
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
int LastModelSpreadPoints = 0;
long LockedAccountLogin = 0;
string LockedAccountServer = "";
string LastCopyStatus = "Ready";
const string UiPrefix = "RAMON_UI_";

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

bool OtherPositionOnSymbol()
{
   for(int i=PositionsTotal()-1;i>=0;i--)
   {
      ulong candidate=PositionGetTicket(i);
      if(candidate==0 || !PositionSelectByTicket(candidate))
         continue;
      if(PositionGetString(POSITION_SYMBOL)==_Symbol
         && (ulong)PositionGetInteger(POSITION_MAGIC)!=MagicNumber)
         return true;
   }
   return false;
}

string BoolText(const bool value)
{
   return (value ? "YES" : "NO");
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
      return "WOULD BLOCK IF SIGNAL: min lot > hard cap";
   return "TRADE BLOCKED: min lot > hard cap";
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
      +"EA version: 0.29\n"
      +"Captured: "+TimeToString(TimeCurrent(),TIME_DATE|TIME_SECONDS)+"\n"
      +"Symbol: "+_Symbol+"  Timeframe: M15\n"
      +"Bid: "+(tick_ok ? DoubleToString(tick.bid,_Digits) : "NA")
      +"  Ask: "+(tick_ok ? DoubleToString(tick.ask,_Digits) : "NA")
      +"  Spread(points): "+IntegerToString(spread_points)+"\n"
      +"Market: "+(SymbolInfoInteger(_Symbol,SYMBOL_TRADE_MODE)==SYMBOL_TRADE_MODE_FULL ? "OPEN/FULL" : "RESTRICTED")
      +"  TerminalConnected: "+BoolText((bool)TerminalInfoInteger(TERMINAL_CONNECTED))+"\n\n"
      +"=== MODEL / SIGNAL ===\n"
      +"ModelUrl: "+ModelUrl+"\n"
      +"Snapshot cadence: "+IntegerToString(SnapshotIntervalSeconds)+"s"
      +"  Last request: "+(LastDecisionRequestTime>0
         ? TimeToString(LastDecisionRequestTime,TIME_DATE|TIME_SECONDS) : "NONE")+"\n"
      +"Decision: "+LastModelDecision+"  Reason: "+LastModelReason+"\n"
      +"DecisionID: "+LastSampleKey+"  Saved: "+BoolText(LastSampleSaved)+"  Bundle: "+LastBundleId+"\n"
      +"TradeLearning: "+TradeLearningStatus+"\n"
      +"BaseDecision: "+LastBaseDecision+"  BaseReason: "+LastBaseReason+"\n"
      +"RoleModels: "+(LastEnsembleReady ? "READY" : "LEARNING")
      +"  Active: "+BoolText(LastEnsembleActive)
      +"  RegimeP: "+DoubleToString(LastRegimeProbability,3)
      +"  EntryP: "+DoubleToString(LastEntryProbability,3)
      +"  NewsP: "+DoubleToString(LastNewsProbability,3)
      +"  MetaP: "+DoubleToString(LastMetaProbability,3)
      +"  RiskReady: "+BoolText(LastRiskModelReady)
      +"  RiskP: "+DoubleToString(LastRiskProbability,3)
      +"  RiskMult: "+DoubleToString(LastRiskMultiplier,2)+"x\n"
      +"News: "+LastNewsSource
      +"  SourceReady: "+BoolText(LastNewsSourceReady)
      +"  ModelReady: "+BoolText(LastNewsModelReady)
      +"  AgeSec: "+DoubleToString(LastNewsSourceAgeSeconds,0)
      +"  Event: "+LastNewsEventCountry+" "+LastNewsEventImpact+" "+LastNewsEventTitle
      +"  DeltaMin: "+DoubleToString(LastNewsEventDeltaMinutes,1)+"\n"
      +"Signal bar: "+(LastSignalBarTime>0 ? TimeToString(LastSignalBarTime,TIME_DATE|TIME_MINUTES) : "NONE")
      +"  Last closed: "+(closed>0 ? TimeToString(closed,TIME_DATE|TIME_MINUTES) : "NONE")+"\n"
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
      +"  TargetDistance: "+DoubleToString(LastTargetDistance,_Digits)+"\n\n"
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
      +"Trades today: "+IntegerToString(today)+"/"+IntegerToString(MaxTradesPerDay)+"\n"
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
   if(!WriteDiagnosticFile || StringLen(DiagnosticFileName)==0)
      return;
   int handle=FileOpen(
      DiagnosticFileName,
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
   color state_color=(LastModelDecision=="BUY" ? clrLime :
      (LastModelDecision=="SELL" ? clrTomato : clrGold));
   string live_reason="";
   bool live_ready=LiveExecutionReady(live_reason);
   color live_color=(live_ready && permissions ? clrLime : clrOrange);
   color risk_gate_color=(RiskGateBlocked() ? clrTomato : clrLime);

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
   color pnl_color=(!has_managed_position ? C'148,163,184' :
      (live_profit_units>0.00001 ? clrLime :
      (live_profit_units<-0.00001 ? clrTomato : clrWhite)));
   string pnl_units=(live_profit_units>0.00001 ? "+" : "")
      +DoubleToString(live_profit_units,2);
   string pnl_usd=(live_profit_usd>0.00001 ? "+$" :
      (live_profit_usd<-0.00001 ? "-$" : "$"))
      +DoubleToString(MathAbs(live_profit_usd),2);

   UiRect("PANEL",12,24,520,574,C'15,23,42',C'71,85,105');
   UiLabel("TITLE","RAMON AI TRADER  v0.30",28,36,clrWhite,12);
   UiLabel("SUB",_Symbol+"  M15  |  Chronos-2  |  live snapshot "
      +IntegerToString(SnapshotIntervalSeconds)+"s",28,56,C'148,163,184',9);

   UiLabel("LIVE","LIVE: "+LiveStateText()
      +"   LOCK: "+(lock_ok ? "OK" : "FAIL")
      +"   PERMS: "+(permissions ? "OK" : "FAIL"),28,82,live_color,10);

   UiLabel("DECISION","DECISION: "+LastModelDecision+"   "+LastModelReason,28,108,state_color,11);
   UiLabel("SIGNAL","Signal: "+(LastSignalBarTime>0 ? TimeToString(LastSignalBarTime,TIME_DATE|TIME_MINUTES) : "NONE")
      +"   Spread: "+IntegerToString(spread_points)+"/"+IntegerToString(MaxSpreadPoints),28,132,clrWhite,9);
   UiLabel("SIGNAL_PRICE","Signal Bid/Ask: "+DoubleToString(LastSignalBid,_Digits)
      +" / "+DoubleToString(LastSignalAsk,_Digits),28,154,C'203,213,225',9);

   UiLabel("FORECAST","Forecast L/M/H: "
      +DoubleToString(LastForecastLow,_Digits)+" / "
      +DoubleToString(LastForecast,_Digits)+" / "
      +DoubleToString(LastForecastHigh,_Digits),28,176,C'191,219,254',9);

   UiLabel("EDGE","Dominant: "+dominant
      +"   BuyEdge: "+DoubleToString(LastBuyEdge,2)
      +"   SellEdge: "+DoubleToString(LastSellEdge,2),28,200,clrWhite,9);
   UiLabel("EDGE_PASS","EDGE "+PassFail(edge_pass)
      +"   "+DoubleToString(dominant_edge,2)+" >= "+DoubleToString(LastMinimumEdge,2),28,222,
      (edge_pass ? clrLime : clrTomato),9);

   UiLabel("STRENGTH","STRENGTH "+PassFail(strength_pass)
      +"   "+DoubleToString(LastSignalStrength,3)+" >= "+DoubleToString(LastMinimumStrength,3)
      +"   Unc: "+DoubleToString(LastUncertainty,2),28,244,
      (strength_pass ? clrLime : clrTomato),9);

   UiLabel("MICRO","INTRABAR "+PassFail(LastIntrabarConfirmed)
      +" "+LastIntrabarDirection
      +"   move "+DoubleToString(LastIntrabarMoveAtr,3)+"/"+DoubleToString(LastIntrabarMinMoveAtr,3)
      +"   rebound "+DoubleToString(LastIntrabarReboundAtr,3)+"/"+DoubleToString(LastIntrabarMinReboundAtr,3),28,266,
      (LastIntrabarConfirmed ? clrLime : C'203,213,225'),9);

   UiLabel("AI_TREND","AI TREND "+PassFail(LastAiTrendConfirmed)
      +" "+LastAiTrendDirection
      +"   score "+DoubleToString(LastAiTrendScore,3)
      +"   path "+DoubleToString(LastAiTrendMoveAtr,3)+" ATR"
      +"   consistency "+DoubleToString(LastAiTrendConsistency,2),28,288,
      (LastAiTrendConfirmed ? clrLime : C'203,213,225'),9);

   UiLabel("ROLE_MODELS","ROLE MODELS "+(LastEnsembleReady ? "READY" : "LEARNING")
      +"   regime "+DoubleToString(LastRegimeProbability,2)
      +"   entry "+DoubleToString(LastEntryProbability,2)
      +"   news "+DoubleToString(LastNewsProbability,2)
      +"   meta "+DoubleToString(LastMetaProbability,2)
      +"   risk "+DoubleToString(LastRiskProbability,2)
      +" x"+DoubleToString(LastRiskMultiplier,2),28,310,
      (LastEnsembleReady ? clrLime : C'203,213,225'),9);

   string news_title=(StringLen(LastNewsEventTitle)>28 ? StringSubstr(LastNewsEventTitle,0,28)+"..." : LastNewsEventTitle);
   string news_delta=(LastNewsEventTime>0
      ? (LastNewsEventDeltaMinutes>=0.0 ? " in " : " ")
         +DoubleToString(MathAbs(LastNewsEventDeltaMinutes),0)+"m"
      : "");
   color news_color=(!LastNewsSourceReady ? clrOrange :
      (LastNewsModelReady ? clrLime : C'203,213,225'));
   UiLabel("NEWS","NEWS "+LastNewsSource
      +" "+(LastNewsSourceReady ? "READY" : "OFFLINE")
      +" | model "+(LastNewsModelReady ? "READY" : "LEARNING")
      +" | "+LastNewsEventImpact+" "+LastNewsEventCountry+" "+news_title+news_delta,
      28,332,news_color,9);

   UiLabel("RISK","ATR: "+DoubleToString(LastAtr,2)
      +"   SL dist: "+DoubleToString(LastStopDistance,2)
      +"   TP dist: "+DoubleToString(LastTargetDistance,2),28,354,C'203,213,225',9);

   UiLabel("ACCOUNT","Account: "+AccountTypeText()+" (configured)"
      +"   Currency: "+AccountInfoString(ACCOUNT_CURRENCY)
      +"   Trades: "+IntegerToString(today)+"/"+IntegerToString(MaxTradesPerDay),28,376,C'203,213,225',9);

   UiLabel("BALANCE_USD","Balance: "+DoubleToString(AccountInfoDouble(ACCOUNT_BALANCE),2)+" units"
      +"   ~= $"+DoubleToString(AccountUnitsToUSD(AccountInfoDouble(ACCOUNT_BALANCE)),2),28,398,C'203,213,225',9);

   UiLabel("LIVE_PNL",
      has_managed_position
         ? "LIVE P/L: "+pnl_units+" units   ~= "+pnl_usd
         : "LIVE P/L: --   (no Ramon position)",
      28,420,pnl_color,10);

   UiLabel("SIZING","Sizing: "+LastSizingSide
      +"   Vol: "+DoubleToString(LastPlannedVolume,2)
      +"   Base: $"+DoubleToString(RiskPerTradeUSD,2)
      +" x"+DoubleToString(LastRiskMultiplier,2)
      +" = $"+DoubleToString(EffectiveRiskPerTradeUSD(),3)
      +"   Cap: $"+DoubleToString(MaxExecutableRiskUSD,2),28,442,
      (ConfirmMoneyUnitsPerUSD ? clrLime : clrOrange),9);

   UiLabel("MIN_RISK","Min executable risk: $"
      +DoubleToString(AccountUnitsToUSD(LastMinimumLotStopLossUnits),4)
      +" ("+DoubleToString(LastMinimumLotStopLossUnits,2)+" units)",28,464,
      risk_gate_color,9);

   UiLabel("RISK_GATE",RiskGateText(),28,486,risk_gate_color,10);

   UiLabel("MONEY_CONFIRM","MoneyUnits/USD: "+DoubleToString(MoneyUnitsPerUSD,2)
      +"   Confirmed: "+BoolText(ConfirmMoneyUnitsPerUSD)
      +(EnableLiveTrading && !live_ready ? "   "+live_reason : ""),28,510,
      (live_ready || !EnableLiveTrading ? clrLime : clrOrange),9);

   UiButton("COPY","COPY DIAGNOSTIC",28,536,176,30);
   UiLabel("COPY_STATUS",LastCopyStatus,218,544,C'148,163,184',8);
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
   LastCopyStatus="COPIED "+TimeToString(TimeCurrent(),TIME_SECONDS);
   return true;
}

void ShowStatus()
{
   WriteDiagnostic();
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
   payload+="],\"quote_time\":"+IntegerToString((long)tick.time)+"}";
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
   double budget=EffectiveRiskPerTradeUSD()*MoneyUnitsPerUSD;
   double hard_cap=MaxExecutableRiskUSD*MoneyUnitsPerUSD;
   if(budget<=0.0 || hard_cap<=0.0 || MoneyUnitsPerUSD<=0.0)
      return false;
   double min_risk=MathAbs(min_loss);
   PendingSizingSampleKey=sample_key;
   PendingSizingRiskBudgetUnits=budget;
   PendingSizingPlannedVolume=volume;
   PendingSizingMinLotSLUnits=min_risk;
   PendingSizingOverrideUsed=(
      AllowMinLotRiskOverride
      && min_risk>budget+0.00001
      && volume<=minimum+0.00000001
      && min_risk<=hard_cap+0.00001
   );
   PendingSizingMaxExecutableRiskUSD=MaxExecutableRiskUSD;
   PendingSizingMoneyUnitsPerUSD=MoneyUnitsPerUSD;
   return true;
}

void UpdateSizingPreview()
{
   LastRiskBudgetUnits=EffectiveRiskPerTradeUSD()*MoneyUnitsPerUSD;
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

void AppendSignalCsv()
{
   if(!WriteCsvLogs || StringLen(SignalCsvFileName)==0)
      return;
   int handle=FileOpen(
      SignalCsvFileName,
      FILE_READ|FILE_WRITE|FILE_CSV|FILE_ANSI|FILE_COMMON,
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
      FileWrite(handle,
         "captured","signal_bar_time","symbol","decision","reason",
         "base_decision","base_reason","ensemble_ready","ensemble_active",
         "regime_probability","entry_probability","meta_probability",
         "risk_model_ready","risk_probability","risk_multiplier",
         "signal_bid","signal_ask","spread_points",
         "forecast_low","forecast_median","forecast_high","atr",
         "buy_edge","sell_edge","minimum_edge","uncertainty",
         "signal_strength","minimum_strength",
         "intrabar_confirmed","intrabar_direction","intrabar_move_atr","intrabar_rebound_atr",
         "intrabar_min_strength","intrabar_min_move_atr","intrabar_min_rebound_atr",
         "ai_trend_confirmed","ai_trend_direction","ai_trend_score",
         "ai_trend_move_atr","ai_trend_consistency",
         "trend_min_path_atr","trend_min_consistency","trend_min_edge_fraction","trend_min_micro_move_atr",
         "stop_distance","target_distance",
         "live_armed","account_lock","account_type","account_currency",
         "balance_units","balance_usd_approx",
         "risk_usd","effective_risk_usd","money_units_per_usd","risk_budget_units",
         "allow_min_lot_override","max_executable_risk_usd","min_lot_override_used",
         "sizing_side","planned_volume","estimated_sl_units","estimated_sl_usd",
         "min_lot_sl_units","min_executable_risk_usd","min_lot_blocked");
   }
   FileWrite(handle,
      TimeToString(TimeCurrent(),TIME_DATE|TIME_SECONDS),
      TimeToString(LastSignalBarTime,TIME_DATE|TIME_MINUTES),
      _Symbol,LastModelDecision,LastModelReason,
      LastBaseDecision,LastBaseReason,BoolText(LastEnsembleReady),BoolText(LastEnsembleActive),
      DoubleToString(LastRegimeProbability,6),DoubleToString(LastEntryProbability,6),
      DoubleToString(LastMetaProbability,6),
      BoolText(LastRiskModelReady),DoubleToString(LastRiskProbability,6),
      DoubleToString(LastRiskMultiplier,6),
      DoubleToString(LastSignalBid,_Digits),DoubleToString(LastSignalAsk,_Digits),
      IntegerToString(LastModelSpreadPoints),
      DoubleToString(LastForecastLow,_Digits),DoubleToString(LastForecast,_Digits),
      DoubleToString(LastForecastHigh,_Digits),DoubleToString(LastAtr,4),
      DoubleToString(LastBuyEdge,_Digits),DoubleToString(LastSellEdge,_Digits),
      DoubleToString(LastMinimumEdge,_Digits),DoubleToString(LastUncertainty,_Digits),
      DoubleToString(LastSignalStrength,6),DoubleToString(LastMinimumStrength,6),
      BoolText(LastIntrabarConfirmed),LastIntrabarDirection,
      DoubleToString(LastIntrabarMoveAtr,6),DoubleToString(LastIntrabarReboundAtr,6),
      DoubleToString(LastIntrabarMinStrength,6),DoubleToString(LastIntrabarMinMoveAtr,6),
      DoubleToString(LastIntrabarMinReboundAtr,6),
      BoolText(LastAiTrendConfirmed),LastAiTrendDirection,DoubleToString(LastAiTrendScore,6),
      DoubleToString(LastAiTrendMoveAtr,6),DoubleToString(LastAiTrendConsistency,6),
      DoubleToString(LastTrendMinPathAtr,6),DoubleToString(LastTrendMinConsistency,6),
      DoubleToString(LastTrendMinEdgeFraction,6),DoubleToString(LastTrendMinMicroMoveAtr,6),
      DoubleToString(LastStopDistance,_Digits),DoubleToString(LastTargetDistance,_Digits),
      BoolText(EnableLiveTrading),BoolText(AccountLockHealthy()),
      AccountTypeText(),AccountInfoString(ACCOUNT_CURRENCY),
      DoubleToString(AccountInfoDouble(ACCOUNT_BALANCE),2),
      DoubleToString(AccountUnitsToUSD(AccountInfoDouble(ACCOUNT_BALANCE)),4),
      DoubleToString(RiskPerTradeUSD,4),DoubleToString(EffectiveRiskPerTradeUSD(),4),
      DoubleToString(MoneyUnitsPerUSD,4),
      DoubleToString(LastRiskBudgetUnits,4),
      BoolText(AllowMinLotRiskOverride),DoubleToString(MaxExecutableRiskUSD,4),
      BoolText(LastMinLotOverrideUsed),LastSizingSide,
      DoubleToString(LastPlannedVolume,4),DoubleToString(LastEstimatedStopLossUnits,4),
      DoubleToString(AccountUnitsToUSD(LastEstimatedStopLossUnits),4),
      DoubleToString(LastMinimumLotStopLossUnits,4),
      DoubleToString(AccountUnitsToUSD(LastMinimumLotStopLossUnits),4),
      BoolText(MinimumLotExceedsRiskBudget()));
   FileFlush(handle);
   FileClose(handle);
}

void AppendTradeCsv(const ulong deal)
{
   if(!WriteCsvLogs || StringLen(TradeCsvFileName)==0 || deal==0 || !HistoryDealSelect(deal))
      return;
   if(HistoryDealGetString(deal,DEAL_SYMBOL)!=_Symbol)
      return;
   if((ulong)HistoryDealGetInteger(deal,DEAL_MAGIC)!=MagicNumber)
      return;

   int handle=FileOpen(
      TradeCsvFileName,
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
         "captured","signal_bar_time","deal","position_id","entry","type",
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

   FileWrite(handle,
      TimeToString(TimeCurrent(),TIME_DATE|TIME_SECONDS),
      (LastSignalBarTime>0 ? TimeToString(LastSignalBarTime,TIME_DATE|TIME_MINUTES) : "NONE"),
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
      version="0.30";
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
      || MathAbs(in_volume-out_volume)>0.000001) return false;
   net=profit+commission+swap+fee;
   string trade_key=AccountInfoString(ACCOUNT_SERVER)+":"
      +IntegerToString(AccountInfoInteger(ACCOUNT_LOGIN))+":"+IntegerToString((long)identifier);
   payload="{\"trade_key\":\""+JsonEscape(trade_key)+"\",\"sample_key\":\""+sample
      +"\",\"symbol\":\""+_Symbol+"\",\"direction\":\""+direction
      +"\",\"opened\":"+IntegerToString((long)opened)+",\"closed\":"+IntegerToString((long)closed)
      +",\"net_units\":"+DoubleToString(net,8)+",\"initial_risk_units\":"+DoubleToString(risk,8)
      +",\"profit_units\":"+DoubleToString(profit,8)
      +",\"commission_units\":"+DoubleToString(commission,8)
      +",\"swap_units\":"+DoubleToString(swap,8)
      +",\"fee_units\":"+DoubleToString(fee,8)
      +",\"exit_reason\":\""+exit_reason+"\"";
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

void ManageOpenPosition()
{
   ulong ticket;
   datetime opened;
   if(!ManagedPosition(ticket,opened)) return;
   int age=iBarShift(_Symbol,PERIOD_M15,opened,false);
   if(age<MaximumHoldBars) { StatusLine="Managed position OPEN"; return; }
   if(Trade.PositionClose(ticket,MaxDeviationPoints))
   {
      RecordDealTelemetry(Trade.ResultDeal(),"maximum_hold_bars");
      StatusLine="TIME EXIT "+IntegerToString(age)+" bars";
   }
   else
      StatusLine="TIME EXIT FAILED "+IntegerToString((int)Trade.ResultRetcode());
}

void OnTimer()
{
   ShowStatus();
   if(!(bool)TerminalInfoInteger(TERMINAL_CONNECTED))
   { StatusLine="Terminal disconnected"; ShowStatus(); return; }
   ulong ticket;
   datetime opened;
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
   { ShowStatus(); return; }
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
      || risk_model_ready<0.0 || risk_model_ready>1.0
      || risk_probability<-1.0 || risk_probability>1.0
      || risk_multiplier<0.50 || risk_multiplier>1.50
      || (risk_model_ready>=0.5 && risk_probability<0.0)
      || (risk_model_ready<0.5 && (MathAbs(risk_multiplier-1.0)>0.000001 || risk_probability!=-1.0))
      || (datetime)signal_time!=bar_time
      || (decision!="BUY" && decision!="SELL" && decision!="WAIT"))
   { StatusLine="Invalid/stale model response"; ShowStatus(); return; }
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
   LastNewsSource=news_source;
   LastNewsSourceReady=(news_source_ready>=0.5);
   LastNewsModelReady=(news_model_ready>=0.5);
   LastNewsSourceAgeSeconds=news_source_age_seconds;
   LastNewsEventTitle=news_event_title;
   LastNewsEventCountry=news_event_country;
   LastNewsEventImpact=news_event_impact;
   LastNewsEventTime=(datetime)news_event_time;
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
   StatusLine=reason;
   UpdateSizingPreview();
   AppendSignalCsv();
   Print("Ramon ",TimeToString(bar_time)," ",decision," ",reason,
      " median=",DoubleToString(median,_Digits));

   // Learning snapshots continue while positions exist; execution remains single-position.
   if(ManagedPosition(ticket,opened))
   { StatusLine=(LastSampleSaved ? "Managed position OPEN; learning snapshot saved" : "Managed position OPEN; snapshot storage failed"); ShowStatus(); return; }
   if(OtherPositionOnSymbol())
   { StatusLine="Another robot has a position on this symbol"; ShowStatus(); return; }
   if(decision!="BUY" && decision!="SELL" && decision!="WAIT")
   { StatusLine="Unknown model decision"; ShowStatus(); return; }
   if(decision=="WAIT" || !EnableLiveTrading)
   { ShowStatus(); return; }
   if(LastEntrySignalBar==bar_time)
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
   int today=TradesToday();
   if(today<0 || today>=MaxTradesPerDay)
   { StatusLine="Daily trade limit/history unavailable"; ShowStatus(); return; }
   MqlTick tick;
   if(!SymbolInfoTick(_Symbol,tick) || TimeCurrent()-tick.time>30
      || (int)SymbolInfoInteger(_Symbol,SYMBOL_SPREAD)>MaxSpreadPoints)
   { StatusLine="Quote changed/stale"; ShowStatus(); return; }
   if(stop_distance<=0.0 || target_distance<=0.0 || atr<=0.0)
   { StatusLine="Invalid stop/target"; ShowStatus(); return; }
   ENUM_ORDER_TYPE side=(decision=="BUY" ? ORDER_TYPE_BUY : ORDER_TYPE_SELL);
   double entry=(decision=="BUY" ? tick.ask : tick.bid);
   double point=SymbolInfoDouble(_Symbol,SYMBOL_POINT);
   double min_stop=(double)SymbolInfoInteger(_Symbol,SYMBOL_TRADE_STOPS_LEVEL)*point;
   stop_distance=MathMax(stop_distance,min_stop+2*point);
   target_distance=MathMax(target_distance,min_stop+2*point);
   double stop=NormalizeDouble(entry+(decision=="BUY" ? -stop_distance : stop_distance),_Digits);
   double target=NormalizeDouble(entry+(decision=="BUY" ? target_distance : -target_distance),_Digits);
   double volume=SelectVolume(side,entry,stop);
   if(volume<=0.0)
   { StatusLine="TRADE BLOCKED: min lot > hard risk cap"; ShowStatus(); return; }
   double margin=0.0;
   if(!OrderCalcMargin(side,_Symbol,volume,entry,margin)
      || margin>AccountInfoDouble(ACCOUNT_MARGIN_FREE)*0.8)
   { StatusLine="Insufficient margin"; ShowStatus(); return; }

   // Telemetry is observational only: failure to stage it must never change execution.
   StageEntrySizing(LastSampleKey,side,entry,stop,volume);
   // The broker owns SL/TP immediately. No position is opened when the model is unavailable.
   bool submitted=(
      decision=="BUY"
      ? Trade.Buy(volume,_Symbol,0.0,stop,target,"Ramon:"+LastSampleKey)
      : Trade.Sell(volume,_Symbol,0.0,stop,target,"Ramon:"+LastSampleKey)
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
      StatusLine="Order sent "+decision+" "+DoubleToString(volume,2);
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

void OnChartEvent(const int id,const long &lparam,const double &dparam,const string &sparam)
{
   if(id!=CHARTEVENT_OBJECT_CLICK || sparam!=UiPrefix+"COPY")
      return;

   ObjectSetInteger(0,sparam,OBJPROP_STATE,false);
   CopyDiagnosticToClipboard();
   DrawDashboard();
}

int OnInit()
{
   if(_Symbol!=TradeSymbol || _Period!=PERIOD_M15 || StringFind(_Symbol,"XAUUSD")!=0)
   { Print("Attach only to ",TradeSymbol," M15"); return INIT_FAILED; }
   if(MoneyUnitsPerUSD<=0.0 || RiskPerTradeUSD<=0.0 || RiskPerTradeUSD>0.50
      || MaxExecutableRiskUSD<=0.0 || MaxExecutableRiskUSD>0.50
      || MaxExecutableRiskUSD<EffectiveRiskPerTradeUSD()
      || MaxSpreadPoints<=0 || MaxTradesPerDay<1 || MaximumHoldBars<1
      || SnapshotIntervalSeconds<10
      || (WriteDiagnosticFile && StringLen(DiagnosticFileName)==0)
      || (WriteCsvLogs && (StringLen(SignalCsvFileName)==0 || StringLen(TradeCsvFileName)==0))
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
   ObjectsDeleteAll(0,UiPrefix);
   ShowStatus();
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   EventKillTimer();
   ObjectsDeleteAll(0,UiPrefix);
   Comment("");
}
