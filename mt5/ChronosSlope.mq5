#property copyright "Ramon AI Trader"
#property version "1.011"
#property indicator_chart_window
#property indicator_plots 0

// WebRequest is prohibited inside MT5 indicators (error 4014).
// Run ChronosSlopeBridge EA on a DIFFERENT chart in the same terminal.
input int RefreshSeconds = 5;
input int VisualLengthMultiplier = 1; // actual horizon
input int ArrowWidth = 3;
input bool ShowLabel = true;
input int MaxDataAgeSeconds = 120;

const string Prefix="CHRONOS_SLOPE_";
double LastCurrentMid=0.0,LastForecastMedian=0.0;
int LastHorizonBars=4;
string LastDirection="NONE";
string LastStatus="Waiting for ChronosSlopeBridge";

string Key(const string suffix) { return Prefix+_Symbol+"_"+suffix; }

void StatusLabel()
{
   string obj=Prefix+"STATUS";
   if(ObjectFind(0,obj)<0) ObjectCreate(0,obj,OBJ_LABEL,0,0,0);
   ObjectSetInteger(0,obj,OBJPROP_CORNER,CORNER_RIGHT_UPPER);
   ObjectSetInteger(0,obj,OBJPROP_ANCHOR,ANCHOR_RIGHT_UPPER);
   ObjectSetInteger(0,obj,OBJPROP_XDISTANCE,30);
   ObjectSetInteger(0,obj,OBJPROP_YDISTANCE,42);
   ObjectSetInteger(0,obj,OBJPROP_COLOR,LastStatus=="OK" ? clrLimeGreen : clrOrange);
   ObjectSetInteger(0,obj,OBJPROP_FONTSIZE,10);
   ObjectSetString(0,obj,OBJPROP_TEXT,"ChronosSlope: "+(LastStatus=="OK" ? LastDirection : LastStatus));
}

bool LoadForecast()
{
   if(!GlobalVariableCheck(Key("UPDATED")) || !GlobalVariableCheck(Key("MID"))
      || !GlobalVariableCheck(Key("MEDIAN")) || !GlobalVariableCheck(Key("DIR"))
      || !GlobalVariableCheck(Key("HORIZON")))
   {
      LastStatus="Bridge not running";
      return false;
   }
   double updated=GlobalVariableGet(Key("UPDATED"));
   if(updated<=0 || (double)TimeCurrent()-updated>MaxDataAgeSeconds)
   {
      LastStatus="Forecast stale";
      return false;
   }
   double mid=GlobalVariableGet(Key("MID"));
   double median=GlobalVariableGet(Key("MEDIAN"));
   int horizon=(int)GlobalVariableGet(Key("HORIZON"));
   int direction=(int)GlobalVariableGet(Key("DIR"));
   if(mid<=0 || median<=0 || horizon<1 || horizon>32 || direction < -1 || direction > 1)
   {
      LastStatus="Invalid bridge data";
      return false;
   }
   LastCurrentMid=mid;
   LastForecastMedian=median;
   LastHorizonBars=horizon;
   LastDirection=direction>0 ? "UP" : direction<0 ? "DOWN" : "FLAT";
   LastStatus="OK";
   return true;
}

void DrawForecast()
{
   string line=Prefix+"LINE",label=Prefix+"LABEL",arrow=Prefix+"ARROW";
   datetime t1=iTime(_Symbol,_Period,0);
   if(t1<=0) t1=TimeCurrent();
   datetime t2=t1+(datetime)(LastHorizonBars*PeriodSeconds(PERIOD_M15)*MathMax(1,VisualLengthMultiplier));
   color clr=LastDirection=="UP" ? clrLimeGreen : LastDirection=="DOWN" ? clrTomato : clrSilver;
   if(ObjectFind(0,line)<0) ObjectCreate(0,line,OBJ_ARROWED_LINE,0,t1,LastCurrentMid,t2,LastForecastMedian);
   else { ObjectMove(0,line,0,t1,LastCurrentMid); ObjectMove(0,line,1,t2,LastForecastMedian); }
   ObjectSetInteger(0,line,OBJPROP_COLOR,clr);
   ObjectSetInteger(0,line,OBJPROP_WIDTH,MathMax(1,ArrowWidth));
   ObjectSetInteger(0,line,OBJPROP_RAY_RIGHT,false);
   ObjectSetInteger(0,line,OBJPROP_SELECTABLE,false);
   // Also place a visible arrow on the current bar, even if the projected line extends off-screen.
   if(ObjectFind(0,arrow)<0) ObjectCreate(0,arrow,OBJ_ARROW,0,t1,LastCurrentMid);
   else ObjectMove(0,arrow,0,t1,LastCurrentMid);
   ObjectSetInteger(0,arrow,OBJPROP_ARROWCODE,LastDirection=="UP" ? 233 : LastDirection=="DOWN" ? 234 : 159);
   ObjectSetInteger(0,arrow,OBJPROP_COLOR,clr);
   ObjectSetInteger(0,arrow,OBJPROP_WIDTH,MathMax(1,ArrowWidth));
   ObjectSetInteger(0,arrow,OBJPROP_SELECTABLE,false);
   if(ShowLabel)
   {
      if(ObjectFind(0,label)<0) ObjectCreate(0,label,OBJ_TEXT,0,t2,LastForecastMedian);
      else ObjectMove(0,label,0,t2,LastForecastMedian);
      ObjectSetString(0,label,OBJPROP_TEXT,"Chronos "+LastDirection+" "+DoubleToString(LastForecastMedian,_Digits)+" ("+IntegerToString(LastHorizonBars*15)+"m)");
      ObjectSetInteger(0,label,OBJPROP_COLOR,clr);
      ObjectSetInteger(0,label,OBJPROP_FONTSIZE,9);
   }
   else ObjectDelete(0,label);
   ChartRedraw(0);
}

void ClearForecast()
{
   ObjectDelete(0,Prefix+"LINE");
   ObjectDelete(0,Prefix+"ARROW");
   ObjectDelete(0,Prefix+"LABEL");
}

int OnInit()
{
   if(RefreshSeconds<1 || VisualLengthMultiplier<1 || VisualLengthMultiplier>12 || MaxDataAgeSeconds<5)
      return INIT_PARAMETERS_INCORRECT;
   EventSetTimer(RefreshSeconds);
   StatusLabel();
   return INIT_SUCCEEDED;
}
void OnDeinit(const int reason) { EventKillTimer(); ObjectsDeleteAll(0,Prefix); }
void OnTimer()
{
   if(LoadForecast()) DrawForecast();
   else ClearForecast();
   StatusLabel();
   ChartRedraw(0);
}
int OnCalculate(const int rates_total,const int prev_calculated,const int begin,const double &price[])
{ return rates_total; }
