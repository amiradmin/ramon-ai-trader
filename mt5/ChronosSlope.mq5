#property copyright "Ramon AI Trader"
#property version "1.035"
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
double LastCurrentMid=0.0,LastForecastMedian=0.0,LastStep15=0.0,LastStep30=0.0,LastStep45=0.0,LastStep60=0.0;
double LastBiasConfidence=0.0,LastBiasScore=0.0;
int LastHorizonBars=4;
string LastDirection="NONE";
string LastBiasDirection="MIXED";
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
   string text="Chronos 60m: "+(LastStatus=="OK" ? LastDirection : LastStatus);
   if(LastStatus=="OK")
      text+=" | Bias: "+LastBiasDirection+" "+DoubleToString(LastBiasConfidence*100.0,0)+"%";
   ObjectSetString(0,obj,OBJPROP_TEXT,text);
}

bool LoadForecast()
{
   if(!GlobalVariableCheck(Key("UPDATED")) || !GlobalVariableCheck(Key("MID"))
      || !GlobalVariableCheck(Key("MEDIAN")) || !GlobalVariableCheck(Key("DIR"))
      || !GlobalVariableCheck(Key("HORIZON"))
      || !GlobalVariableCheck(Key("STEP1")) || !GlobalVariableCheck(Key("STEP2"))
      || !GlobalVariableCheck(Key("STEP3")) || !GlobalVariableCheck(Key("STEP4"))
      || !GlobalVariableCheck(Key("BIAS_DIR")) || !GlobalVariableCheck(Key("BIAS_CONF"))
      || !GlobalVariableCheck(Key("BIAS_SCORE")))
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
   double step45=GlobalVariableGet(Key("STEP3"));
   double step60=GlobalVariableGet(Key("STEP4"));
   int direction=(int)GlobalVariableGet(Key("DIR"));
   int bias_direction=(int)GlobalVariableGet(Key("BIAS_DIR"));
   double bias_confidence=GlobalVariableGet(Key("BIAS_CONF"));
   double bias_score=GlobalVariableGet(Key("BIAS_SCORE"));
   if(mid<=0 || median<=0 || step15<=0 || step30<=0 || step45<=0 || step60<=0 || horizon!=4
      || direction < -1 || direction > 1 || bias_direction < -1 || bias_direction > 1
      || bias_confidence<0.0 || bias_confidence>1.0 || !MathIsValidNumber(bias_score))
   {
      LastStatus="Invalid bridge data";
      return false;
   }
   LastCurrentMid=mid;
   LastForecastMedian=median;
   LastStep15=step15;
   LastStep30=step30;
   LastStep45=step45;
   LastStep60=step60;
   LastHorizonBars=horizon;
   LastDirection=direction>0 ? "UP" : direction<0 ? "DOWN" : "FLAT";
   LastBiasDirection=bias_direction>0 ? "BUY" : bias_direction<0 ? "SELL" : "MIXED";
   LastBiasConfidence=bias_confidence;
   LastBiasScore=bias_score;
   LastStatus="OK";
   return true;
}

// Forecast path: current mid -> +15m -> +30m -> +45m -> +60m.
// Drawn from the latest quote time.  Display-only; Ramon execution is independent.
void DrawForecast()
{
   datetime times[5];
   double prices[5];
   times[0]=TimeCurrent();
   for(int i=1;i<=4;i++) times[i]=times[0]+i*PeriodSeconds(PERIOD_M15);
   prices[0]=LastCurrentMid;
   prices[1]=LastStep15;
   prices[2]=LastStep30;
   prices[3]=LastStep45;
   prices[4]=LastStep60;
   color overall=LastDirection=="UP" ? clrLimeGreen : LastDirection=="DOWN" ? clrTomato : clrSilver;

   for(int i=0;i<4;i++)
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

   for(int j=1;j<=4;j++)
   {
      string point=Prefix+"POINT"+IntegerToString(j);
      if(ObjectFind(0,point)<0) ObjectCreate(0,point,OBJ_ARROW,0,times[j],prices[j]);
      else ObjectMove(0,point,0,times[j],prices[j]);
      ObjectSetInteger(0,point,OBJPROP_ARROWCODE,159);
      ObjectSetInteger(0,point,OBJPROP_WIDTH,2);
      color endpoint_color=(j==4 && LastBiasConfidence>=0.50)
         ? (LastBiasDirection=="BUY" ? clrLimeGreen : LastBiasDirection=="SELL" ? clrTomato : clrSilver)
         : (j==4 ? overall : clrAqua);
      ObjectSetInteger(0,point,OBJPROP_COLOR,endpoint_color);
      ObjectSetInteger(0,point,OBJPROP_SELECTABLE,false);

      string label=Prefix+"STEP_LABEL"+IntegerToString(j);
      if(ShowLabel)
      {
         if(ObjectFind(0,label)<0) ObjectCreate(0,label,OBJ_TEXT,0,times[j],prices[j]);
         else ObjectMove(0,label,0,times[j],prices[j]);
         ObjectSetString(0,label,OBJPROP_TEXT,
            "Chronos +"+IntegerToString(j*15)+"m "+DoubleToString(prices[j],_Digits));
         ObjectSetInteger(0,label,OBJPROP_COLOR,j==4 ? overall : clrAqua);
         ObjectSetInteger(0,label,OBJPROP_FONTSIZE,9);
         ObjectSetInteger(0,label,OBJPROP_ANCHOR,(j%2==1) ? ANCHOR_RIGHT_LOWER : ANCHOR_LEFT_UPPER);
         ObjectSetInteger(0,label,OBJPROP_SELECTABLE,false);
      }
      else ObjectDelete(0,label);
   }

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
   for(int i=1;i<=4;i++)
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
