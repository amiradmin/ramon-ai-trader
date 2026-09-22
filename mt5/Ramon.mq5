#property strict
#property version "0.14"
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
input double MoneyUnitsPerUSD = 100.0; // LiteFinance cent account, verified manually.
input double RiskPerTradeUSD = 0.06;
input int MaxSpreadPoints = 50;
input int MaxTradesPerDay = 4;
input int MaximumHoldBars = 4;
input int RequestTimeoutMs = 4000;
input int MaxDeviationPoints = 30;
input ulong MagicNumber = 26092212;
input bool WriteDiagnosticFile = true;
input string DiagnosticFileName = "Ramon_Diagnostic.txt";
input bool ShowDashboard = true;
input bool EnableClipboardButton = true;

CTrade Trade;
datetime LastProcessedBar = 0;
string StatusLine = "Starting";
string LastModelDecision = "NONE";
string LastModelReason = "NONE";
datetime LastSignalBarTime = 0;
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
      +"EA version: 0.14\n"
      +"Captured: "+TimeToString(TimeCurrent(),TIME_DATE|TIME_SECONDS)+"\n"
      +"Symbol: "+_Symbol+"  Timeframe: M15\n"
      +"Bid: "+(tick_ok ? DoubleToString(tick.bid,_Digits) : "NA")
      +"  Ask: "+(tick_ok ? DoubleToString(tick.ask,_Digits) : "NA")
      +"  Spread(points): "+IntegerToString(spread_points)+"\n"
      +"Market: "+(SymbolInfoInteger(_Symbol,SYMBOL_TRADE_MODE)==SYMBOL_TRADE_MODE_FULL ? "OPEN/FULL" : "RESTRICTED")
      +"  TerminalConnected: "+BoolText((bool)TerminalInfoInteger(TERMINAL_CONNECTED))+"\n\n"
      +"=== MODEL / SIGNAL ===\n"
      +"ModelUrl: "+ModelUrl+"\n"
      +"Decision: "+LastModelDecision+"  Reason: "+LastModelReason+"\n"
      +"Signal bar: "+(LastSignalBarTime>0 ? TimeToString(LastSignalBarTime,TIME_DATE|TIME_MINUTES) : "NONE")
      +"  Last closed: "+(closed>0 ? TimeToString(closed,TIME_DATE|TIME_MINUTES) : "NONE")+"\n"
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
      +"StopDistance: "+DoubleToString(LastStopDistance,_Digits)
      +"  TargetDistance: "+DoubleToString(LastTargetDistance,_Digits)+"\n\n"
      +"=== ACCOUNT / EXECUTION ===\n"
      +"Live: "+(EnableLiveTrading ? "ARMED" : "DISARMED")
      +"  AccountLock: "+(AccountLockHealthy() ? "OK" : "FAIL")
      +"  Server: "+AccountInfoString(ACCOUNT_SERVER)+"\n"
      +"Balance: "+DoubleToString(AccountInfoDouble(ACCOUNT_BALANCE),2)
      +"  Equity: "+DoubleToString(AccountInfoDouble(ACCOUNT_EQUITY),2)
      +"  FreeMargin: "+DoubleToString(AccountInfoDouble(ACCOUNT_MARGIN_FREE),2)+"\n"
      +"Trade permissions: terminal="+BoolText((bool)TerminalInfoInteger(TERMINAL_TRADE_ALLOWED))
      +" ea="+BoolText((bool)MQLInfoInteger(MQL_TRADE_ALLOWED))
      +" account="+BoolText((bool)AccountInfoInteger(ACCOUNT_TRADE_ALLOWED))+"\n"
      +"Status: "+StatusLine+"\n"
      +"Managed position: "+position_line+"\n"
      +"Trades today: "+IntegerToString(today)+"/"+IntegerToString(MaxTradesPerDay)+"\n"
      +"RiskPerTradeUSD: "+DoubleToString(RiskPerTradeUSD,2)
      +"  MoneyUnitsPerUSD: "+DoubleToString(MoneyUnitsPerUSD,2)
      +"  MaxSpreadPoints: "+IntegerToString(MaxSpreadPoints)+"\n";

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
   color live_color=(EnableLiveTrading && lock_ok && permissions ? clrLime : clrOrange);

   UiRect("PANEL",12,24,448,324,C'15,23,42',C'71,85,105');
   UiLabel("TITLE","RAMON AI TRADER  v0.14",28,36,clrWhite,12);
   UiLabel("SUB",_Symbol+"  M15  |  Chronos-2",28,56,C'148,163,184',9);

   UiLabel("LIVE","LIVE: "+(EnableLiveTrading ? "ARMED" : "DISARMED")
      +"   LOCK: "+(lock_ok ? "OK" : "FAIL")
      +"   PERMS: "+(permissions ? "OK" : "FAIL"),28,82,live_color,10);

   UiLabel("DECISION","DECISION: "+LastModelDecision+"   "+LastModelReason,28,108,state_color,11);
   UiLabel("SIGNAL","Signal: "+(LastSignalBarTime>0 ? TimeToString(LastSignalBarTime,TIME_DATE|TIME_MINUTES) : "NONE")
      +"   Spread: "+IntegerToString(spread_points)+"/"+IntegerToString(MaxSpreadPoints),28,132,clrWhite,9);

   UiLabel("FORECAST","Forecast L/M/H: "
      +DoubleToString(LastForecastLow,_Digits)+" / "
      +DoubleToString(LastForecast,_Digits)+" / "
      +DoubleToString(LastForecastHigh,_Digits),28,154,C'191,219,254',9);

   UiLabel("EDGE","Dominant: "+dominant
      +"   BuyEdge: "+DoubleToString(LastBuyEdge,2)
      +"   SellEdge: "+DoubleToString(LastSellEdge,2),28,178,clrWhite,9);
   UiLabel("EDGE_PASS","EDGE "+PassFail(edge_pass)
      +"   "+DoubleToString(dominant_edge,2)+" >= "+DoubleToString(LastMinimumEdge,2),28,200,
      (edge_pass ? clrLime : clrTomato),9);

   UiLabel("STRENGTH","STRENGTH "+PassFail(strength_pass)
      +"   "+DoubleToString(LastSignalStrength,3)+" >= "+DoubleToString(LastMinimumStrength,3)
      +"   Unc: "+DoubleToString(LastUncertainty,2),28,222,
      (strength_pass ? clrLime : clrTomato),9);

   UiLabel("RISK","ATR: "+DoubleToString(LastAtr,2)
      +"   SL dist: "+DoubleToString(LastStopDistance,2)
      +"   TP dist: "+DoubleToString(LastTargetDistance,2),28,244,C'203,213,225',9);

   UiLabel("ACCOUNT","Balance: "+DoubleToString(AccountInfoDouble(ACCOUNT_BALANCE),2)
      +"   Trades: "+IntegerToString(today)+"/"+IntegerToString(MaxTradesPerDay)
      +"   Risk: $"+DoubleToString(RiskPerTradeUSD,2),28,266,C'203,213,225',9);

   UiButton("COPY","COPY DIAGNOSTIC",28,294,176,30);
   UiLabel("COPY_STATUS",LastCopyStatus,218,302,C'148,163,184',8);
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
   if((int)SymbolInfoInteger(_Symbol,SYMBOL_SPREAD)>MaxSpreadPoints)
   { StatusLine="Spread too high"; return false; }
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
   payload+="]}";
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
   double budget=RiskPerTradeUSD*MoneyUnitsPerUSD;
   if(budget<=0.0 || -money>budget+0.00001)
      return 0.0; // Broker minimum lot does not fit USD risk.
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

void ManageOpenPosition()
{
   ulong ticket;
   datetime opened;
   if(!ManagedPosition(ticket,opened)) return;
   int age=iBarShift(_Symbol,PERIOD_M15,opened,false);
   if(age<MaximumHoldBars) { StatusLine="Managed position OPEN"; return; }
   if(Trade.PositionClose(ticket,MaxDeviationPoints))
      StatusLine="TIME EXIT "+IntegerToString(age)+" bars";
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
   if(ManagedPosition(ticket,opened))
   { ManageOpenPosition(); ShowStatus(); return; }
   if(OtherPositionOnSymbol())
   { StatusLine="Another robot has a position on this symbol"; ShowStatus(); return; }
   datetime closed=iTime(_Symbol,PERIOD_M15,1);
   if(closed<=0 || closed==LastProcessedBar)
      return;

   string payload,reply;
   datetime bar_time=0;
   if(!BuildRequest(payload,bar_time) || !QueryModel(payload,reply))
   { ShowStatus(); return; }
   string decision="",reason="";
   double signal_time=0.0,median=0.0,atr=0.0,stop_distance=0.0,target_distance=0.0;
   double forecast_low=0.0,forecast_high=0.0,edge=0.0,model_spread=0.0;
   double buy_edge=0.0,sell_edge=0.0,minimum_edge=0.0,uncertainty=0.0,signal_strength=0.0,minimum_strength=0.0;
   if(!JsonText(reply,"decision",decision)
      || !JsonText(reply,"reason",reason)
      || !JsonNumber(reply,"signal_bar_time",signal_time)
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
      || !JsonNumber(reply,"spread_points",model_spread)
      || !JsonNumber(reply,"stop_distance",stop_distance)
      || !JsonNumber(reply,"target_distance",target_distance)
      || (datetime)signal_time!=bar_time
      || (decision!="BUY" && decision!="SELL" && decision!="WAIT"))
   { StatusLine="Invalid/stale model response"; ShowStatus(); return; }
   LastProcessedBar=bar_time; // At most one entry attempt per closed candle.
   LastModelDecision=decision;
   LastModelReason=reason;
   LastSignalBarTime=bar_time;
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
   LastModelSpreadPoints=(int)model_spread;
   LastStopDistance=stop_distance;
   LastTargetDistance=target_distance;
   StatusLine=reason;
   Print("Ramon ",TimeToString(bar_time)," ",decision," ",reason,
      " median=",DoubleToString(median,_Digits));

   if(decision=="WAIT" || !EnableLiveTrading)
   { ShowStatus(); return; }
   if(!AccountLockHealthy())
   { StatusLine="Account/server lock mismatch"; ShowStatus(); return; }
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
   { StatusLine="Minimum lot exceeds risk budget"; ShowStatus(); return; }
   double margin=0.0;
   if(!OrderCalcMargin(side,_Symbol,volume,entry,margin)
      || margin>AccountInfoDouble(ACCOUNT_MARGIN_FREE)*0.8)
   { StatusLine="Insufficient margin"; ShowStatus(); return; }
   // The broker owns SL/TP immediately. No position is opened when the model is unavailable.
   bool submitted=(
      decision=="BUY"
      ? Trade.Buy(volume,_Symbol,0.0,stop,target,"Ramon")
      : Trade.Sell(volume,_Symbol,0.0,stop,target,"Ramon")
   );
   uint retcode=Trade.ResultRetcode();
   if(!submitted || (retcode!=TRADE_RETCODE_DONE && retcode!=TRADE_RETCODE_PLACED))
      StatusLine="Order rejected "+IntegerToString((int)retcode);
   else
      StatusLine="Order sent "+decision+" "+DoubleToString(volume,2);
   Print("Ramon execution: ",StatusLine);
   ShowStatus();
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
      || MaxSpreadPoints<=0 || MaxTradesPerDay<1 || MaximumHoldBars<1
      || (WriteDiagnosticFile && StringLen(DiagnosticFileName)==0)
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
   if(EnableLiveTrading && !AccountLockHealthy())
   { Print("Account/server lock required before arming"); return INIT_FAILED; }
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
