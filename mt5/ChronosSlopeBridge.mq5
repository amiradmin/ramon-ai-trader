#property copyright "Ramon AI Trader"
#property version "1.023"
#property strict

// Read-only bridge. Attach to a SEPARATE chart from the Ramon trading EA.
// Publishes forecast data through terminal Global Variables for ChronosSlope indicator.
input string ForecastUrl="http://127.0.0.1:8012/forecast-only";
input string ForecastSymbol="XAUUSD_l";
input int RequestTimeoutMs=4000;
input int RefreshSeconds=15;
input int ForecastHorizonBars=4; // 60-minute forecast: four M15 steps
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
   int copied=CopyRates(ForecastSymbol,PERIOD_M15,0,256,bars); // Display-only: include the live/forming M15 bar so the slope can adapt intrabar.
   if(copied<128) { Print("ChronosSlopeBridge: need >=128 M15 bars including current"); return false; }
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
   string direction="",model="",bias_direction="",bias_source="";
   double mid=0,median=0,horizon=0,step1=0,step2=0,step3=0,step4=0,conf1=0,conf2=0,conf3=0,conf4=0,bias_confidence=0,bias_score=0;
   if(!JsonText(reply,"direction",direction) || !JsonText(reply,"model",model)
      || !JsonNumber(reply,"current_mid",mid) || !JsonNumber(reply,"forecast_median",median)
       || !JsonNumber(reply,"forecast_horizon_bars",horizon)
      || !JsonNumber(reply,"forecast_step_1",step1)
      || !JsonNumber(reply,"forecast_step_2",step2)
      || !JsonNumber(reply,"forecast_step_3",step3)
      || !JsonNumber(reply,"forecast_step_4",step4)
      || !JsonNumber(reply,"forecast_step_confidence_1",conf1)
      || !JsonNumber(reply,"forecast_step_confidence_2",conf2)
      || !JsonNumber(reply,"forecast_step_confidence_3",conf3)
      || !JsonNumber(reply,"forecast_step_confidence_4",conf4)
      || !JsonText(reply,"bias_direction",bias_direction)
      || !JsonText(reply,"bias_source",bias_source)
      || !JsonNumber(reply,"bias_confidence",bias_confidence)
      || !JsonNumber(reply,"bias_score",bias_score)
      || mid<=0 || median<=0 || step1<=0 || step2<=0 || step3<=0 || step4<=0
      || conf1<0 || conf1>1 || conf2<0 || conf2>1 || conf3<0 || conf3>1 || conf4<0 || conf4>1 || horizon!=4)
   { Print("ChronosSlopeBridge: invalid forecast response: ",StringSubstr(reply,0,300)); return; }
   int dir=direction=="UP" ? 1 : direction=="DOWN" ? -1 : 0;
   GlobalVariableSet(Key("MID"),mid);
   GlobalVariableSet(Key("MEDIAN"),median);
   GlobalVariableSet(Key("HORIZON"),horizon);
   GlobalVariableSet(Key("STEP1"),step1);
   GlobalVariableSet(Key("STEP2"),step2);
   GlobalVariableSet(Key("STEP3"),step3);
   GlobalVariableSet(Key("STEP4"),step4);
   GlobalVariableSet(Key("CONF1"),conf1);
   GlobalVariableSet(Key("CONF2"),conf2);
   GlobalVariableSet(Key("CONF3"),conf3);
   GlobalVariableSet(Key("CONF4"),conf4);
   int bias_dir=bias_direction=="BUY" ? 1 : bias_direction=="SELL" ? -1 : 0;
   GlobalVariableSet(Key("DIR"),dir);
   GlobalVariableSet(Key("BIAS_DIR"),bias_dir);
   GlobalVariableSet(Key("BIAS_CONF"),bias_confidence);
   GlobalVariableSet(Key("BIAS_SCORE"),bias_score);
   GlobalVariableSet(Key("UPDATED"),(double)TimeCurrent()); // publish last
   Print("ChronosSlopeBridge: ",direction,
      " bias=",bias_direction,
      " conf=",DoubleToString(bias_confidence,3),
      " score=",DoubleToString(bias_score,3),
      " mid=",mid," forecast=",median," step15=",step1," step30=",step2,
      " step45=",step3," step60=",step4,
      " conf=",DoubleToString(conf1,2),"/",DoubleToString(conf2,2),"/",DoubleToString(conf3,2),"/",DoubleToString(conf4,2),
      " horizon=",horizon);
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
