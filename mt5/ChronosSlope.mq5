#property copyright "Ramon AI Trader"
#property version "1.033"
#property indicator_chart_window
#property indicator_plots 0

// WebRequest is prohibited inside MT5 indicators (error 4014).
// Run ChronosSlopeBridge EA on a DIFFERENT chart in the same terminal.
input int RefreshSeconds = 5;
input int VisualLengthMultiplier = 1; // actual horizon
input int ArrowWidth = 3;
input bool ShowLabel = false; // keep chart clean; endpoint labels are optional
input int MaxDataAgeSeconds = 120;

const string Prefix="CHRONOS_SLOPE_";
double LastCurrentMid=0.0,LastForecastMedian=0.0,LastStep15=0.0,LastStep30=0.0;
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
      || !GlobalVariableCheck(Key("HORIZON"))
      || !GlobalVariableCheck(Key("STEP1")) || !GlobalVariableCheck(Key("STEP2")))
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
   double step15=GlobalVariableGet(Key("STEP1"));
   double step30=GlobalVariableGet(Key("STEP2"));
   int direction=(int)GlobalVariableGet(Key("DIR"));
   if(mid<=0 || median<=0 || step15<=0 || step30<=0 || horizon!=2 || direction < -1 || direction > 1)
   {
      LastStatus="Invalid bridge data";
      return false;
   }
   LastCurrentMid=mid;
   LastForecastMedian=median;
   LastStep15=step15;
   LastStep30=step30;
   LastHorizonBars=horizon;
   LastDirection=direction>0 ? "UP" : direction<0 ? "DOWN" : "FLAT";
   LastStatus="OK";
   return true;
}

// Forecast path: current mid -> +15m -> +30m.
// Drawn from the latest quote time, not a stretched endpoint.
void DrawForecast()
{
   datetime t0=TimeCurrent();
   datetime t15=t0+PeriodSeconds(PERIOD_M15);
   datetime t30=t15+PeriodSeconds(PERIOD_M15);
   color overall=LastDirection=="UP" ? clrLimeGreen : LastDirection=="DOWN" ? clrTomato : clrSilver;
   double prices[3];
   datetime times[3];
   prices[0]=LastCurrentMid; prices[1]=LastStep15; prices[2]=LastStep30;
   times[0]=t0; times[1]=t15; times[2]=t30;
   for(int i=0;i<2;i++)
   {
      string name=Prefix+"SEG"+IntegerToString(i+1);
      color segment=prices[i+1]>prices[i] ? clrLimeGreen : prices[i+1]<prices[i] ? clrTomato : clrSilver;
      if(ObjectFind(0,name)<0)
         ObjectCreate(0,name,OBJ_TREND,0,times[i],prices[i],times[i+1],prices[i+1]);
      else
      {
         ObjectMove(0,name,0,times[i],prices[i]);
         ObjectMove(0,name,1,times[i+1],prices[i+1]);
      }
      ObjectSetInteger(0,name,OBJPROP_RAY_RIGHT,false);
      ObjectSetInteger(0,name,OBJPROP_RAY_LEFT,false);
      ObjectSetInteger(0,name,OBJPROP_COLOR,segment);
      ObjectSetInteger(0,name,OBJPROP_WIDTH,MathMax(1,ArrowWidth));
      ObjectSetInteger(0,name,OBJPROP_SELECTABLE,false);
      ObjectSetInteger(0,name,OBJPROP_BACK,false);
   }
   // Projected endpoints are two independently forecast model outputs.
   for(int j=1;j<=2;j++)
   {
      string point=Prefix+"POINT"+IntegerToString(j);
      if(ObjectFind(0,point)<0) ObjectCreate(0,point,OBJ_ARROW,0,times[j],prices[j]);
      else ObjectMove(0,point,0,times[j],prices[j]);
      ObjectSetInteger(0,point,OBJPROP_ARROWCODE,159);
      ObjectSetInteger(0,point,OBJPROP_WIDTH,2);
      ObjectSetInteger(0,point,OBJPROP_COLOR,j==2 ? overall : clrAqua);
      ObjectSetInteger(0,point,OBJPROP_SELECTABLE,false);
      string label=Prefix+"STEP_LABEL"+IntegerToString(j);
      if(ShowLabel)
      {
         if(ObjectFind(0,label)<0) ObjectCreate(0,label,OBJ_TEXT,0,times[j],prices[j]);
         else ObjectMove(0,label,0,times[j],prices[j]);
         ObjectSetString(0,label,OBJPROP_TEXT,
            "Chronos +"+IntegerToString(j*15)+"m "+DoubleToString(prices[j],_Digits));
         ObjectSetInteger(0,label,OBJPROP_COLOR,j==2 ? overall : clrAqua);
         ObjectSetInteger(0,label,OBJPROP_FONTSIZE,9);
         // Put 15m text to the left/above and 30m text right/below.
         // This prevents text overlap even when forecast prices nearly match.
         ObjectSetInteger(0,label,OBJPROP_ANCHOR,j==1 ? ANCHOR_RIGHT_LOWER : ANCHOR_LEFT_UPPER);
         ObjectSetInteger(0,label,OBJPROP_SELECTABLE,false);
      }
      else ObjectDelete(0,label);
   }
   // Remove legacy single-arrow objects during upgrade.
   ObjectDelete(0,Prefix+"LINE");
   ObjectDelete(0,Prefix+"ARROW");
   ObjectDelete(0,Prefix+"LABEL");
   ChartRedraw(0);
}
void ClearForecast()
{
   ObjectDelete(0,Prefix+"LINE");
   ObjectDelete(0,Prefix+"ARROW");
   ObjectDelete(0,Prefix+"LABEL");
   for(int i=1;i<=2;i++)
   {
      ObjectDelete(0,Prefix+"SEG"+IntegerToString(i));
      ObjectDelete(0,Prefix+"POINT"+IntegerToString(i));
      ObjectDelete(0,Prefix+"STEP_LABEL"+IntegerToString(i));
   }
}

int OnInit()
{
   if(RefreshSeconds<1 || VisualLengthMultiplier<1 || VisualLengthMultiplier>12
      || MaxDataAgeSeconds<5)
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
