#property copyright "Ramon AI Trader"
#property version   "1.000"
#property indicator_chart_window
#property indicator_plots 0

input string ForecastUrl = "http://127.0.0.1:8012/forecast-only";
input int RequestTimeoutMs = 4000;
input int RefreshSeconds = 5;
input int VisualLengthMultiplier = 3;
input int ArrowWidth = 3;
input bool ShowLabel = true;

const string Prefix = "CHRONOS_SLOPE_";
datetime LastRequest = 0;
string LastDirection = "NONE";
double LastCurrentMid = 0.0;
double LastForecastMedian = 0.0;
int LastHorizonBars = 4;
string LastModel = "";
string LastStatus = "Waiting";

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

bool BuildRequest(string &payload)
{
   MqlRates bars[];
   ArraySetAsSeries(bars,true);
   int copied=CopyRates(_Symbol,PERIOD_M15,1,256,bars);
   if(copied<128)
   {
      LastStatus="Need >=128 completed M15 bars";
      return false;
   }

   MqlTick tick;
   if(!SymbolInfoTick(_Symbol,tick) || tick.bid<=0.0 || tick.ask<=tick.bid)
   {
      LastStatus="Bad tick";
      return false;
   }
   if(TimeCurrent()-tick.time>30)
   {
      LastStatus="Stale tick";
      return false;
   }

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
   payload+="],\"micro_bars\":[],\"quote_time\":"+IntegerToString((long)tick.time)+"}";
   return true;
}

bool QueryForecast()
{
   string payload="";
   if(!BuildRequest(payload))
      return false;

   char request[],response[];
   StringToCharArray(payload,request,0,WHOLE_ARRAY,CP_UTF8);
   ArrayResize(request,ArraySize(request)-1);
   string headers="Content-Type: application/json\r\n";
   string response_headers="";
   ResetLastError();
   int code=WebRequest("POST",ForecastUrl,headers,RequestTimeoutMs,request,response,response_headers);
   if(code!=200)
   {
      LastStatus="HTTP "+IntegerToString(code)+" err "+IntegerToString(GetLastError());
      return false;
   }

   string reply=CharArrayToString(response,0,ArraySize(response),CP_UTF8);
   double current_mid=0.0,median=0.0,horizon=0.0;
   string direction="",model="";
   if(!JsonText(reply,"direction",direction)
      || !JsonText(reply,"model",model)
      || !JsonNumber(reply,"current_mid",current_mid)
      || !JsonNumber(reply,"forecast_median",median)
      || !JsonNumber(reply,"forecast_horizon_bars",horizon)
      || current_mid<=0.0 || median<=0.0 || horizon<1.0)
   {
      LastStatus="Invalid forecast response";
      return false;
   }

   LastDirection=direction;
   LastModel=model;
   LastCurrentMid=current_mid;
   LastForecastMedian=median;
   LastHorizonBars=(int)horizon;
   LastStatus="OK";
   return true;
}

void DrawForecast()
{
   string line=Prefix+"LINE";
   string label=Prefix+"LABEL";

   if(LastCurrentMid<=0.0 || LastForecastMedian<=0.0)
   {
      ObjectDelete(0,line);
      ObjectDelete(0,label);
      return;
   }

   int multiplier=MathMax(1,VisualLengthMultiplier);
   datetime t1=TimeCurrent();
   datetime t2=t1+(datetime)(LastHorizonBars*PeriodSeconds(PERIOD_M15)*multiplier);

   color clr=clrSilver;
   if(LastDirection=="UP") clr=clrLimeGreen;
   else if(LastDirection=="DOWN") clr=clrTomato;

   if(ObjectFind(0,line)<0)
      ObjectCreate(0,line,OBJ_ARROWED_LINE,0,t1,LastCurrentMid,t2,LastForecastMedian);
   else
   {
      ObjectMove(0,line,0,t1,LastCurrentMid);
      ObjectMove(0,line,1,t2,LastForecastMedian);
   }

   ObjectSetInteger(0,line,OBJPROP_COLOR,clr);
   ObjectSetInteger(0,line,OBJPROP_WIDTH,MathMax(1,ArrowWidth));
   ObjectSetInteger(0,line,OBJPROP_STYLE,STYLE_SOLID);
   ObjectSetInteger(0,line,OBJPROP_BACK,false);
   ObjectSetInteger(0,line,OBJPROP_SELECTABLE,false);
   ObjectSetInteger(0,line,OBJPROP_HIDDEN,true);
   ObjectSetString(0,line,OBJPROP_TOOLTIP,
      "Standalone Chronos | "+LastDirection
      +" | real horizon "+IntegerToString(LastHorizonBars)+"×M15"
      +" | visual x"+IntegerToString(multiplier)
      +" | "+DoubleToString(LastCurrentMid,_Digits)
      +" → "+DoubleToString(LastForecastMedian,_Digits));

   if(ShowLabel)
   {
      string text="Chronos "+LastDirection+"  "
         +DoubleToString(LastForecastMedian,_Digits)
         +"  ("+IntegerToString(LastHorizonBars*15)+"m)";
      if(ObjectFind(0,label)<0)
         ObjectCreate(0,label,OBJ_TEXT,0,t2,LastForecastMedian);
      else
         ObjectMove(0,label,0,t2,LastForecastMedian);
      ObjectSetString(0,label,OBJPROP_TEXT,text);
      ObjectSetInteger(0,label,OBJPROP_COLOR,clr);
      ObjectSetInteger(0,label,OBJPROP_FONTSIZE,9);
      ObjectSetInteger(0,label,OBJPROP_SELECTABLE,false);
      ObjectSetInteger(0,label,OBJPROP_HIDDEN,true);
   }
   else
      ObjectDelete(0,label);

   ChartRedraw(0);
}

int OnInit()
{
   if(RefreshSeconds<1 || RequestTimeoutMs<500 || VisualLengthMultiplier<1 || VisualLengthMultiplier>12)
      return INIT_PARAMETERS_INCORRECT;
   EventSetTimer(1);
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   EventKillTimer();
   ObjectsDeleteAll(0,Prefix);
}

void OnTimer()
{
   if(TimeCurrent()-LastRequest<RefreshSeconds)
      return;
   LastRequest=TimeCurrent();
   if(QueryForecast())
      DrawForecast();
}

int OnCalculate(const int rates_total,const int prev_calculated,const int begin,const double &price[])
{
   return rates_total;
}
