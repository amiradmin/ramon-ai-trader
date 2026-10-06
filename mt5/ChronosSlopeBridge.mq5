#property copyright "Ramon AI Trader"
#property version "1.020"
#property strict

// Read-only bridge. Attach to a SEPARATE chart from the Ramon trading EA.
// Publishes forecast data through terminal Global Variables for ChronosSlope indicator.
input string ForecastUrl="http://127.0.0.1:8012/forecast-only";
input string ForecastSymbol="XAUUSD_l";
input int RequestTimeoutMs=4000;
input int RefreshSeconds=15;
input int ForecastHorizonBars=2; // 30-minute M15 forecast
const string Prefix="CHRONOS_SLOPE_";

string Key(const string suffix) { return Prefix+ForecastSymbol+"_"+suffix; }

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
   while(start<StringLen(json) && StringGetCharacter(json,start)==32) start++;
   int finish=start;
   while(finish<StringLen(json))
   {
      ushort c=StringGetCharacter(json,finish);
      if((c>=48 && c<=57) || c==45 || c==46 || c==43 || c==69 || c==101) finish++;
      else break;
   }
   if(finish<=start) return false;
   value=StringToDouble(StringSubstr(json,start,finish-start));
   return MathIsValidNumber(value);
}
bool BuildRequest(string &payload)
{
   MqlRates bars[];
   ArraySetAsSeries(bars,true);
   int copied=CopyRates(ForecastSymbol,PERIOD_M15,1,256,bars);
   if(copied<128) { Print("ChronosSlopeBridge: need >=128 closed M15 bars"); return false; }
   MqlTick tick;
   if(!SymbolInfoTick(ForecastSymbol,tick) || tick.bid<=0 || tick.ask<=tick.bid)
   { Print("ChronosSlopeBridge: invalid tick"); return false; }
   if(TimeCurrent()-tick.time>30)
   { Print("ChronosSlopeBridge: stale tick"); return false; }
   int digits=(int)SymbolInfoInteger(ForecastSymbol,SYMBOL_DIGITS);
   payload="{\"symbol\":\""+ForecastSymbol+"\",\"timeframe\":\"M15\",\"bid\":"
      +DoubleToString(tick.bid,digits)+",\"ask\":"+DoubleToString(tick.ask,digits)
      +",\"point\":"+DoubleToString(SymbolInfoDouble(ForecastSymbol,SYMBOL_POINT),digits)+",\"bars\":[";
   for(int i=copied-1;i>=0;i--)
   {
      if(i<copied-1) payload+=",";
      payload+="{\"time\":"+IntegerToString((long)bars[i].time)
         +",\"open\":"+DoubleToString(bars[i].open,digits)
         +",\"high\":"+DoubleToString(bars[i].high,digits)
         +",\"low\":"+DoubleToString(bars[i].low,digits)
         +",\"close\":"+DoubleToString(bars[i].close,digits)+"}";
   }
   payload+="],\"micro_bars\":[],\"quote_time\":"+IntegerToString((long)tick.time)
      +",\"forecast_horizon_bars\":"+IntegerToString(ForecastHorizonBars)+"}";
   return true;
}
void Refresh()
{
   string payload;
   if(!BuildRequest(payload)) return;
   char request[],response[];
   StringToCharArray(payload,request,0,WHOLE_ARRAY,CP_UTF8);
   ArrayResize(request,ArraySize(request)-1);
   string response_headers="";
   ResetLastError();
   int code=WebRequest("POST",ForecastUrl,"Content-Type: application/json\r\n",
                       RequestTimeoutMs,request,response,response_headers);
   if(code!=200)
   {
      Print("ChronosSlopeBridge: forecast HTTP ",code," MT5 err ",GetLastError(),
            " (allow URL in Tools > Options > Expert Advisors)");
      return;
   }
   string reply=CharArrayToString(response,0,ArraySize(response),CP_UTF8);
   string direction="",model="";
   double mid=0,median=0,horizon=0,step1=0,step2=0;
   if(!JsonText(reply,"direction",direction) || !JsonText(reply,"model",model)
      || !JsonNumber(reply,"current_mid",mid) || !JsonNumber(reply,"forecast_median",median)
       || !JsonNumber(reply,"forecast_horizon_bars",horizon)
      || !JsonNumber(reply,"forecast_step_1",step1)
      || !JsonNumber(reply,"forecast_step_2",step2)
      || mid<=0 || median<=0 || step1<=0 || step2<=0 || horizon!=2)
   { Print("ChronosSlopeBridge: invalid forecast response: ",StringSubstr(reply,0,300)); return; }
   int dir=direction=="UP" ? 1 : direction=="DOWN" ? -1 : 0;
   GlobalVariableSet(Key("MID"),mid);
   GlobalVariableSet(Key("MEDIAN"),median);
   GlobalVariableSet(Key("HORIZON"),horizon);
   GlobalVariableSet(Key("STEP1"),step1);
   GlobalVariableSet(Key("STEP2"),step2);
   GlobalVariableSet(Key("DIR"),dir);
   GlobalVariableSet(Key("UPDATED"),(double)TimeCurrent()); // publish last
   Print("ChronosSlopeBridge: ",direction," mid=",mid," forecast=",median," step15=",step1," step30=",step2," horizon=",horizon);
}
int OnInit()
{
   if(RefreshSeconds<5 || RequestTimeoutMs<500 || ForecastSymbol=="" || ForecastHorizonBars<1 || ForecastHorizonBars>16) return INIT_PARAMETERS_INCORRECT;
   if(!SymbolSelect(ForecastSymbol,true)) return INIT_FAILED;
   EventSetTimer(RefreshSeconds);
   return INIT_SUCCEEDED;
}
void OnDeinit(const int reason) { EventKillTimer(); }
void OnTimer() { Refresh(); }
void OnTick() {}
