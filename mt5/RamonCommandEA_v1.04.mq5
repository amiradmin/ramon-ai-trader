#property strict
#property version   "1.04"
#property description "RamonCommandEA v1.04: DST-safe UTC candle telemetry + guarded demo execution"
#define RAMON_COMMAND_EA_VERSION "1.04"

#include <Trade/Trade.mqh>

input string ApiBaseUrl = "http://127.0.0.1:8017/api/ea/v1";
input string DjangoBearerToken = "";       // Set locally in MT5; never commit a real token.
input string InstanceId = "ramon-command-ea-1";
input bool EnableCommandExecution = false; // Must remain false until demo validation is complete.
input int TimerSeconds = 15;
input int DecisionIntervalSeconds = 30; // Django decision requests, independent of telemetry polling.
input int BarsToSend = 128;
input int RequestTimeoutMs = 5000;
input int MaxSpreadPoints = 80;
input double MaxRiskMoney = 5.0;           // Account-currency risk ceiling per OPEN command.

CTrade Trade;
string LastCommandId = "";
datetime LastTelemetryAttempt = 0;
datetime LastDecisionAttempt = 0;
bool Busy = false;
struct BrokerOffsetEpoch { datetime first_server_time; long offset_seconds; };
struct BrokerOffsetGap { datetime gap_start; datetime gap_end; };
BrokerOffsetEpoch BrokerOffsetEpochs[];
BrokerOffsetGap BrokerOffsetGaps[];
datetime BrokerClockStartedAt=0, BrokerClockLastSampleAt=0;
long BrokerClockLastOffset=0;
bool BrokerClockReady=false, BrokerClockReliable=true;

string JsonEscape(string value)
{
   StringReplace(value, "\\", "\\\\");
   StringReplace(value, "\"", "\\\"");
   StringReplace(value, "\r", "\\r");
   StringReplace(value, "\n", "\\n");
   StringReplace(value, "\t", "\\t");
   return value;
}

string JsonString(const string json, const string key, const string fallback="")
{
   string needle = "\"" + key + "\"";
   int p = StringFind(json, needle);
   if(p < 0) return fallback;
   p = StringFind(json, ":", p + StringLen(needle));
   if(p < 0) return fallback;
   p++;
   while(p < StringLen(json) && (StringGetCharacter(json,p)==' ' || StringGetCharacter(json,p)=='\t'))
      p++;
   if(p >= StringLen(json) || StringGetCharacter(json,p)!='\"') return fallback;
   p++;
   string out = "";
   bool escaped = false;
   for(int i=p; i<StringLen(json); i++)
   {
      ushort c = StringGetCharacter(json,i);
      if(escaped)
      {
         if(c=='n') out += "\n";
         else if(c=='r') out += "\r";
         else if(c=='t') out += "\t";
         else out += ShortToString(c);
         escaped = false;
      }
      else if(c=='\\') escaped = true;
      else if(c=='\"') return out;
      else out += ShortToString(c);
   }
   return fallback;
}

double JsonNumber(const string json, const string key, const double fallback=0.0)
{
   string needle = "\"" + key + "\"";
   int p = StringFind(json, needle);
   if(p < 0) return fallback;
   p = StringFind(json, ":", p + StringLen(needle));
   if(p < 0) return fallback;
   p++;
   while(p < StringLen(json) && (StringGetCharacter(json,p)==' ' || StringGetCharacter(json,p)=='\t'))
      p++;
   int end = p;
   while(end < StringLen(json))
   {
      ushort c = StringGetCharacter(json,end);
      if(c==',' || c=='}' || c==']' || c==' ' || c=='\r' || c=='\n') break;
      end++;
   }
   if(end<=p) return fallback;
   return StringToDouble(StringSubstr(json,p,end-p));
}

bool JsonBool(const string json, const string key, const bool fallback=false)
{
   string needle = "\"" + key + "\"";
   int p = StringFind(json, needle);
   if(p < 0) return fallback;
   p = StringFind(json, ":", p + StringLen(needle));
   if(p < 0) return fallback;
   string tail = StringSubstr(json,p+1,8);
   StringTrimLeft(tail);
   return StringFind(tail,"true")==0;
}


string JsonObject(const string json, const string key)
{
   string needle="\"" + key + "\"";
   int p=StringFind(json,needle);
   if(p<0) return "";
   p=StringFind(json,":",p+StringLen(needle));
   if(p<0) return "";
   p++;
   while(p<StringLen(json) &&
         (StringGetCharacter(json,p)==' ' ||
          StringGetCharacter(json,p)=='\t' ||
          StringGetCharacter(json,p)=='\r' ||
          StringGetCharacter(json,p)=='\n'))
      p++;
   if(p>=StringLen(json) || StringGetCharacter(json,p)!='{') return "";

   int begin=p;
   int depth=0;
   bool in_string=false;
   bool escaped=false;

   for(int i=p;i<StringLen(json);i++)
   {
      ushort c=StringGetCharacter(json,i);
      if(in_string)
      {
         if(escaped) escaped=false;
         else if(c=='\\') escaped=true;
         else if(c=='\"') in_string=false;
         continue;
      }
      if(c=='\"') in_string=true;
      else if(c=='{') depth++;
      else if(c=='}')
      {
         depth--;
         if(depth==0) return StringSubstr(json,begin,i-begin+1);
      }
   }
   return "";
}

string IsoUtc(datetime stamp)
{
   MqlDateTime d;
   TimeToStruct(stamp,d);
   return StringFormat("%04d-%02d-%02dT%02d:%02d:%02dZ",d.year,d.mon,d.day,d.hour,d.min,d.sec);
}

// Broker offsets are civil-time offsets, normally in 15-minute increments.
// Rounding removes sub-minute jitter caused by sampling second-resolution clocks
// on opposite sides of a second boundary.
long StableBrokerUtcOffsetSeconds()
{
   datetime server_now=TimeTradeServer();
   datetime utc_now=TimeGMT();
   if(server_now<=0 || utc_now<=0) return 0;
   long raw_offset=(long)(server_now-utc_now);
   long stable_offset=(long)(MathRound((double)raw_offset/900.0)*900.0);
   if(MathAbs((double)(raw_offset-stable_offset))>120.0)
      Print("RAMON TIME WARNING: raw broker UTC offset=",raw_offset,
            "s; normalized offset=",stable_offset,
            "s. Check broker/server clock configuration.");
   return stable_offset;
}

bool SampleBrokerClock()
{
   datetime server_now=TimeTradeServer(), utc_now=TimeGMT();
   if(server_now<=0 || utc_now<=0) return false;
   long raw=(long)(server_now-utc_now);
   long offset=(long)(MathRound((double)raw/900.0)*900.0);
   if(MathAbs((double)(raw-offset))>120.0) { BrokerClockReliable=false; return false; }
   if(!BrokerClockReady)
   {
      ArrayResize(BrokerOffsetEpochs,1);
      BrokerOffsetEpochs[0].first_server_time=server_now;
      BrokerOffsetEpochs[0].offset_seconds=offset;
      BrokerClockStartedAt=server_now; BrokerClockLastSampleAt=server_now;
      BrokerClockLastOffset=offset; BrokerClockReady=true;
      Print("RAMON TIME: offset history initialized; pre-start candles withheld.");
      return true;
   }
   if(server_now<BrokerClockLastSampleAt) { BrokerClockReliable=false; return false; }
   if(server_now==BrokerClockLastSampleAt)
   {
      if(offset!=BrokerClockLastOffset) BrokerClockReliable=false;
      return BrokerClockReliable;
   }
   if(offset!=BrokerClockLastOffset)
   {
      int g=ArraySize(BrokerOffsetGaps), e=ArraySize(BrokerOffsetEpochs);
      if(g>=64 || e>=64) { BrokerClockReliable=false; return false; }
      ArrayResize(BrokerOffsetGaps,g+1);
      BrokerOffsetGaps[g].gap_start=BrokerClockLastSampleAt;
      BrokerOffsetGaps[g].gap_end=server_now;
      ArrayResize(BrokerOffsetEpochs,e+1);
      BrokerOffsetEpochs[e].first_server_time=server_now;
      BrokerOffsetEpochs[e].offset_seconds=offset;
      Print("RAMON TIME WARNING: broker offset changed; candles in unsampled transition interval rejected.");
   }
   BrokerClockLastSampleAt=server_now; BrokerClockLastOffset=offset;
   return BrokerClockReliable;
}
bool BrokerBarToUtc(const datetime broker_time,datetime &utc_time)
{
   utc_time=0;
   if(!BrokerClockReady || !BrokerClockReliable || broker_time<BrokerClockStartedAt || broker_time>BrokerClockLastSampleAt) return false;
   for(int i=0;i<ArraySize(BrokerOffsetGaps);i++)
      if(broker_time>=BrokerOffsetGaps[i].gap_start && broker_time<=BrokerOffsetGaps[i].gap_end) return false;
   int selected=-1;
   for(int i=0;i<ArraySize(BrokerOffsetEpochs);i++)
   {
      if(BrokerOffsetEpochs[i].first_server_time<=broker_time) selected=i;
      else break;
   }
   if(selected<0) return false;
   utc_time=(datetime)((long)broker_time-BrokerOffsetEpochs[selected].offset_seconds);
   return utc_time>0;
}

datetime UtcFromIso(string value)
{
   // Accept only explicit UTC timestamps: ...Z or ...+00:00.
   // StructToTime builds the UTC calendar fields without local-time parsing.
   int n=StringLen(value);
   if(n<20) return 0;

   if(StringGetCharacter(value,4)!='-' ||
      StringGetCharacter(value,7)!='-' ||
      StringGetCharacter(value,10)!='T' ||
      StringGetCharacter(value,13)!=':' ||
      StringGetCharacter(value,16)!=':')
      return 0;

   bool utc_suffix=false;
   if(StringSubstr(value,n-1,1)=="Z")
      utc_suffix=true;
   else if(n>=25 && StringSubstr(value,n-6,6)=="+00:00")
      utc_suffix=true;
   if(!utc_suffix) return 0;

   // If fractional seconds exist, require digits after the decimal point.
   if(n>20 && StringGetCharacter(value,19)=='.')
   {
      int suffix_start=(StringSubstr(value,n-1,1)=="Z") ? n-1 : n-6;
      if(suffix_start<=20) return 0;
      for(int i=20;i<suffix_start;i++)
      {
         ushort c=StringGetCharacter(value,i);
         if(c<'0' || c>'9') return 0;
      }
   }
   else
   {
      int expected=(StringSubstr(value,n-1,1)=="Z") ? 20 : 25;
      if(n!=expected) return 0;
   }

   MqlDateTime d;
   ZeroMemory(d);
   d.year=(int)StringToInteger(StringSubstr(value,0,4));
   d.mon=(int)StringToInteger(StringSubstr(value,5,2));
   d.day=(int)StringToInteger(StringSubstr(value,8,2));
   d.hour=(int)StringToInteger(StringSubstr(value,11,2));
   d.min=(int)StringToInteger(StringSubstr(value,14,2));
   d.sec=(int)StringToInteger(StringSubstr(value,17,2));

   if(d.year<1970 || d.mon<1 || d.mon>12 || d.day<1 || d.day>31 ||
      d.hour<0 || d.hour>23 || d.min<0 || d.min>59 ||
      d.sec<0 || d.sec>59)
      return 0;

   datetime stamp=StructToTime(d);
   if(stamp<=0) return 0;

   // Reject normalized invalid dates such as February 31.
   MqlDateTime check;
   if(!TimeToStruct(stamp,check)) return 0;
   if(check.year!=d.year || check.mon!=d.mon || check.day!=d.day ||
      check.hour!=d.hour || check.min!=d.min || check.sec!=d.sec)
      return 0;

   return stamp;
}

string TfName(ENUM_TIMEFRAMES tf)
{
   if(tf==PERIOD_M1) return "M1";
   if(tf==PERIOD_M5) return "M5";
   if(tf==PERIOD_M15) return "M15";
   if(tf==PERIOD_M30) return "M30";
   if(tf==PERIOD_H1) return "H1";
   if(tf==PERIOD_H4) return "H4";
   if(tf==PERIOD_D1) return "D1";
   return EnumToString(tf);
}

bool PostJson(const string path,const string body,string &response)
{
   if(StringLen(DjangoBearerToken)<32) return false;
   string headers="Content-Type: application/json\r\nAuthorization: Bearer "+DjangoBearerToken+"\r\n";
   char data[], result[];
   StringToCharArray(body,data,0,WHOLE_ARRAY,CP_UTF8);
   if(ArraySize(data)>0) ArrayResize(data,ArraySize(data)-1);
   string response_headers;
   ResetLastError();
   int code=WebRequest("POST",ApiBaseUrl+path,headers,RequestTimeoutMs,data,result,response_headers);
   if(code<200 || code>=300)
   {
      Print("RamonCommandEA HTTP failure path=",path," code=",code," error=",GetLastError());
      return false;
   }
   response=CharArrayToString(result,0,-1,CP_UTF8);
   if(path=="/decision")
      Print("RamonCommandEA v1.04: Django decision HTTP success code=",code,
            " response_bytes=",ArraySize(result));
   return true;
}

string BuildBarsJson()
{
   MqlRates rates[];
   ArraySetAsSeries(rates,true);
   int count=MathMax(1,MathMin(BarsToSend,512));
   int copied=CopyRates(_Symbol,_Period,1,count,rates);
   if(copied<=0 || !BrokerClockReady || !BrokerClockReliable) return "[]";
   string out="[";
   int emitted=0;
   for(int i=copied-1;i>=0;i--)
   {
      datetime utc=0;
      if(!BrokerBarToUtc(rates[i].time,utc)) continue;
      if(emitted>0) out+=",";
      out+="{\"timestamp\":\""+IsoUtc(utc)+"\",\"open\":"+DoubleToString(rates[i].open,_Digits)+",\"high\":"+DoubleToString(rates[i].high,_Digits)+",\"low\":"+DoubleToString(rates[i].low,_Digits)+",\"close\":"+DoubleToString(rates[i].close,_Digits)+",\"volume\":"+DoubleToString((double)rates[i].tick_volume,0)+",\"closed\":true}";
      emitted++;
   }
   return out+"]";
}


string BuildDecisionBarsJson()
{
   if(_Period!=PERIOD_M15 || !BrokerClockReady || !BrokerClockReliable) return "[]";
   MqlRates rates[];
   ArraySetAsSeries(rates,true);
   int count=MathMax(128,MathMin(BarsToSend,512));
   int copied=CopyRates(_Symbol,PERIOD_M15,1,count,rates);
   if(copied<128) return "[]";
   string out="[";
   for(int i=copied-1;i>=0;i--)
   {
      datetime utc=0;
      // Never shorten a decision batch: any pre-start or ambiguous candle rejects it.
      if(!BrokerBarToUtc(rates[i].time,utc)) return "[]";
      if(i<copied-1) out+=",";
      out+="{\"time\":"+IntegerToString((long)utc)+",\"open\":"+DoubleToString(rates[i].open,_Digits)+",\"high\":"+DoubleToString(rates[i].high,_Digits)+",\"low\":"+DoubleToString(rates[i].low,_Digits)+",\"close\":"+DoubleToString(rates[i].close,_Digits)+"}";
   }
   return out+"]";
}

double CalculateDailyLossPercent()
{
   double balance=AccountInfoDouble(ACCOUNT_BALANCE);
   datetime server_now=TimeTradeServer();
   if(balance<=0 || server_now<=0) return 100.0;

   MqlDateTime day;
   TimeToStruct(server_now,day);
   day.hour=0; day.min=0; day.sec=0;
   datetime day_start=StructToTime(day);
   if(day_start<=0 || !HistorySelect(day_start,server_now)) return 100.0;

   double realized=0.0;
   for(int i=0;i<HistoryDealsTotal();i++)
   {
      ulong deal=HistoryDealGetTicket(i);
      if(deal==0) continue;
      ENUM_DEAL_ENTRY entry=(ENUM_DEAL_ENTRY)HistoryDealGetInteger(deal,DEAL_ENTRY);
      if(entry!=DEAL_ENTRY_OUT && entry!=DEAL_ENTRY_OUT_BY && entry!=DEAL_ENTRY_INOUT)
         continue;
      realized+=HistoryDealGetDouble(deal,DEAL_PROFIT)
               +HistoryDealGetDouble(deal,DEAL_SWAP)
               +HistoryDealGetDouble(deal,DEAL_COMMISSION)
               +HistoryDealGetDouble(deal,DEAL_FEE);
   }
   return MathMax(0.0,-realized)/balance*100.0;
}

double CalculateOpenRiskPercent()
{
   double balance=AccountInfoDouble(ACCOUNT_BALANCE);
   if(balance<=0) return 100.0;
   double risk_money=0.0;

   for(int i=0;i<PositionsTotal();i++)
   {
      ulong ticket=PositionGetTicket(i);
      if(ticket==0) continue;
      double sl=PositionGetDouble(POSITION_SL);
      double volume=PositionGetDouble(POSITION_VOLUME);
      double entry=PositionGetDouble(POSITION_PRICE_OPEN);
      ENUM_POSITION_TYPE side=(ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE);

      // A position without a stop loss has unbounded/unknown risk; fail closed.
      if(sl<=0 || volume<=0 || entry<=0) return 100.0;
      ENUM_ORDER_TYPE order_type=(side==POSITION_TYPE_BUY ? ORDER_TYPE_BUY : ORDER_TYPE_SELL);
      double pnl_at_sl=0.0;
      if(!OrderCalcProfit(order_type,PositionGetString(POSITION_SYMBOL),
                          volume,entry,sl,pnl_at_sl))
         return 100.0;
      if(pnl_at_sl<0) risk_money+=MathAbs(pnl_at_sl);
   }
   return risk_money/balance*100.0;
}

double CalculateDecisionTakeProfitPoints()
{
   double point=SymbolInfoDouble(_Symbol,SYMBOL_POINT);
   if(point<=0) return 0.0;

   MqlRates rates[];
   ArraySetAsSeries(rates,true);
   if(CopyRates(_Symbol,PERIOD_M15,1,16,rates)<16) return 0.0;

   double total_range=0.0;
   for(int i=0;i<14;i++)
   {
      double previous_close=rates[i+1].close;
      double tr=MathMax(rates[i].high-rates[i].low,
                MathMax(MathAbs(rates[i].high-previous_close),
                        MathAbs(rates[i].low-previous_close)));
      total_range+=tr;
   }
   double atr=total_range/14.0;
   double stop_level=(double)SymbolInfoInteger(_Symbol,SYMBOL_TRADE_STOPS_LEVEL);
   return MathMax(stop_level+1.0,2.0*atr/point);
}

void RequestDjangoDecision()
{
   Print("RamonCommandEA v1.04: decision poll started; symbol=", _Symbol,
         " timeframe=", TfName(_Period),
         " execution_enabled=", EnableCommandExecution);
   if(_Period!=PERIOD_M15)
   {
      Print("RamonCommandEA: Django decisions require an M15 chart.");
      return;
   }

   string bars=BuildDecisionBarsJson();
   if(StringLen(bars)<3 || bars=="[]")
   {
      Print("RamonCommandEA: decision skipped; need at least 128 completed M15 bars and valid server time.");
      return;
   }

   MqlTick tick;
   if(!SymbolInfoTick(_Symbol,tick) || tick.bid<=0 || tick.ask<tick.bid)
   {
      Print("RamonCommandEA: decision skipped; quote is invalid.");
      return;
   }

   double point=SymbolInfoDouble(_Symbol,SYMBOL_POINT);
   double balance=AccountInfoDouble(ACCOUNT_BALANCE);
   double tick_size=SymbolInfoDouble(_Symbol,SYMBOL_TRADE_TICK_SIZE);
   double tick_value=SymbolInfoDouble(_Symbol,SYMBOL_TRADE_TICK_VALUE);
   double point_value_per_lot=(tick_size>0 ? tick_value*point/tick_size : 0.0);
   double risk_percent=(balance>0 ? MaxRiskMoney/balance*100.0 : 0.0);
   double take_profit_points=CalculateDecisionTakeProfitPoints();
   double daily_loss_percent=CalculateDailyLossPercent();
   double open_risk_percent=CalculateOpenRiskPercent();

   string body="{\"protocol_version\":\"ramon-django-decision-v1\","
      +"\"instance_id\":\""+JsonEscape(InstanceId)+"\","
      +"\"account_login\":\""+IntegerToString((long)AccountInfoInteger(ACCOUNT_LOGIN))+"\","
      // Match the broker identity sent in telemetry for Django account-state validation.
      +"\"server_name\":\""+JsonEscape(AccountInfoString(ACCOUNT_SERVER))+"\","
      +"\"symbol\":\""+JsonEscape(_Symbol)+"\",\"timeframe\":\"M15\","
      +"\"bid\":"+DoubleToString(tick.bid,_Digits)+","
      +"\"ask\":"+DoubleToString(tick.ask,_Digits)+","
      +"\"point\":"+DoubleToString(point,10)+","
      +"\"bars\":"+bars+","
      +"\"account_balance\":"+DoubleToString(balance,2)+","
      +"\"risk_percent\":"+DoubleToString(risk_percent,8)+","
      +"\"point_value_per_lot\":"+DoubleToString(point_value_per_lot,8)+","
      +"\"volume_step\":"+DoubleToString(SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_STEP),8)+","
      +"\"min_volume\":"+DoubleToString(SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_MIN),8)+","
      +"\"max_volume\":"+DoubleToString(SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_MAX),8)+","
      +"\"stop_level_points\":"+DoubleToString((double)SymbolInfoInteger(_Symbol,SYMBOL_TRADE_STOPS_LEVEL),2)+","
      +"\"take_profit_points\":"+DoubleToString(take_profit_points,2)+","
      +"\"daily_loss_percent\":"+DoubleToString(daily_loss_percent,8)+","
      +"\"open_risk_percent\":"+DoubleToString(open_risk_percent,8)+"}";

   string response;
   if(!PostJson("/decision",body,response))
   {
      Print("RamonCommandEA: Django decision request failed; no local decision or order generated.");
      return;
   }

   string decision=JsonString(response,"decision","UNKNOWN");
   string reason=JsonString(response,"reason","NO_REASON");
   string sample_key=JsonString(response,"sample_key","");
   Print("RamonCommandEA Django decision: ",decision,
         " reason=",reason,
         " sample_key=",sample_key,
         " trading_authorization=",JsonBool(response,"trading_authorization",false),
         ". EA does not execute decision responses; broker execution remains command-only.");
}

string BuildPositionsJson()
{
   string out="[";
   bool first=true;
   for(int i=0;i<PositionsTotal();i++)
   {
      ulong ticket=PositionGetTicket(i);
      if(ticket==0) continue;
      if(!first) out+=",";
      first=false;
      out+="{\"ticket\":\""+IntegerToString((long)ticket)+"\","
         +"\"symbol\":\""+JsonEscape(PositionGetString(POSITION_SYMBOL))+"\","
         +"\"type\":"+IntegerToString((int)PositionGetInteger(POSITION_TYPE))+","
         +"\"volume\":"+DoubleToString(PositionGetDouble(POSITION_VOLUME),8)+","
         +"\"price_open\":"+DoubleToString(PositionGetDouble(POSITION_PRICE_OPEN),_Digits)+","
         +"\"sl\":"+DoubleToString(PositionGetDouble(POSITION_SL),_Digits)+","
         +"\"tp\":"+DoubleToString(PositionGetDouble(POSITION_TP),_Digits)+","
         +"\"profit\":"+DoubleToString(PositionGetDouble(POSITION_PROFIT),2)+"}";
   }
   return out+"]";
}

string BuildTelemetry()
{
   MqlTick tick;
   if(!SymbolInfoTick(_Symbol,tick) || tick.bid<=0 || tick.ask<tick.bid) return "";
   double point=SymbolInfoDouble(_Symbol,SYMBOL_POINT);
   if(point<=0) return "";
   datetime server_now=TimeTradeServer();
   if(server_now<=0 || !BrokerClockReady || !BrokerClockReliable) return "";
   datetime quote_utc=0;
   if(!BrokerBarToUtc(tick.time,quote_utc)) return "";
   // The risk baseline key uses the broker's calendar day, not local PC or UTC date.
   MqlDateTime broker_day_parts;
   TimeToStruct(server_now,broker_day_parts);
   string broker_day_text=StringFormat("%04d-%02d-%02d",
      broker_day_parts.year,broker_day_parts.mon,broker_day_parts.day);
   string account="{\"currency\":\""+JsonEscape(AccountInfoString(ACCOUNT_CURRENCY))+"\","
      +"\"balance\":"+DoubleToString(AccountInfoDouble(ACCOUNT_BALANCE),2)+","
      +"\"equity\":"+DoubleToString(AccountInfoDouble(ACCOUNT_EQUITY),2)+","
      +"\"margin\":"+DoubleToString(AccountInfoDouble(ACCOUNT_MARGIN),2)+","
      +"\"free_margin\":"+DoubleToString(AccountInfoDouble(ACCOUNT_MARGIN_FREE),2)+"}";
   return "{\"protocol_version\":\"ramon-ea-command-v1\","
      +"\"instance_id\":\""+JsonEscape(InstanceId)+"\","
      +"\"account_login\":\""+IntegerToString((long)AccountInfoInteger(ACCOUNT_LOGIN))+"\","
      +"\"server_name\":\""+JsonEscape(AccountInfoString(ACCOUNT_SERVER))+"\","
      +"\"account_trade_mode\":"+IntegerToString((int)AccountInfoInteger(ACCOUNT_TRADE_MODE))+","
      +"\"ea_version\":\""+RAMON_COMMAND_EA_VERSION+"\","
      +"\"broker_day\":\""+broker_day_text+"\","
      +"\"symbol\":\""+JsonEscape(_Symbol)+"\",\"timeframe\":\""+TfName(_Period)+"\","
      +"\"quote\":{\"bid\":"+DoubleToString(tick.bid,_Digits)+",\"ask\":"+DoubleToString(tick.ask,_Digits)+",\"point\":"+DoubleToString(point,_Digits)+"},"
      +"\"quote_time\":\""+IsoUtc(quote_utc)+"\","
      +"\"terminal_trade_allowed\":"+((bool)TerminalInfoInteger(TERMINAL_TRADE_ALLOWED) && (bool)MQLInfoInteger(MQL_TRADE_ALLOWED) ? "true":"false")+","
      +"\"account\":"+account+",\"positions\":"+BuildPositionsJson()+","
      +"\"last_command_id\":\""+JsonEscape(LastCommandId)+"\",\"bars\":"+BuildBarsJson()+"}";
}

bool AckCommand(const string command_id,const string state,const string detail,const ulong ticket=0)
{
   string result="{\"detail\":\""+JsonEscape(detail)+"\",\"broker_ticket\":\""+IntegerToString((long)ticket)+"\"}";
   string body="{\"command_id\":\""+JsonEscape(command_id)+"\","
      +"\"instance_id\":\""+JsonEscape(InstanceId)+"\","
      +"\"account_login\":\""+IntegerToString((long)AccountInfoInteger(ACCOUNT_LOGIN))+"\","
      +"\"state\":\""+state+"\",\"result\":"+result+"}";
   string response;
   return PostJson("/commands/ack",body,response);
}

bool SafeOpen(const string command_json,const string command_id,ulong &ticket_out,string &reason)
{
   if(!EnableCommandExecution) { reason="LOCAL_EXECUTION_DISABLED"; return false; }
   if(AccountInfoInteger(ACCOUNT_TRADE_MODE)!=ACCOUNT_TRADE_MODE_DEMO)
   { reason="DEMO_ACCOUNT_REQUIRED"; return false; }
   if(!TerminalInfoInteger(TERMINAL_TRADE_ALLOWED) || !MQLInfoInteger(MQL_TRADE_ALLOWED))
   { reason="TERMINAL_TRADE_PERMISSION_DENIED"; return false; }

   string side=JsonString(command_json,"side","");
   double volume=JsonNumber(command_json,"volume",0);
   double sl=JsonNumber(command_json,"stop_loss",0);
   double tp=JsonNumber(command_json,"take_profit",0);
   MqlTick tick;
   if(!SymbolInfoTick(_Symbol,tick) || tick.bid<=0 || tick.ask<tick.bid)
   { reason="INVALID_QUOTE"; return false; }
   double point=SymbolInfoDouble(_Symbol,SYMBOL_POINT);
   double min_volume=SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_MIN);
   double max_volume=SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_MAX);
   double step=SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_STEP);
   if(point<=0 || min_volume<=0 || step<=0 || volume<min_volume || volume>max_volume)
   { reason="INVALID_VOLUME_OR_SYMBOL"; return false; }
   if(MathAbs(volume/step-MathRound(volume/step))>1e-6)
   { reason="VOLUME_STEP_MISMATCH"; return false; }
   if((tick.ask-tick.bid)/point>MaxSpreadPoints)
   { reason="SPREAD_TOO_WIDE"; return false; }

   ENUM_ORDER_TYPE order_type;
   double entry=0;
   if(side=="BUY") { order_type=ORDER_TYPE_BUY; entry=tick.ask; if(sl<=0 || sl>=entry || tp<=entry) { reason="INVALID_BUY_SL_TP"; return false; } }
   else if(side=="SELL") { order_type=ORDER_TYPE_SELL; entry=tick.bid; if(sl<=entry || tp<=0 || tp>=entry) { reason="INVALID_SELL_SL_TP"; return false; } }
   else { reason="INVALID_SIDE"; return false; }

   double loss=0;
   if(!OrderCalcProfit(order_type,_Symbol,volume,entry,sl,loss) || loss>=0 || MathAbs(loss)>MaxRiskMoney)
   { reason="RISK_LIMIT_OR_CALCULATION_FAILED"; return false; }

   bool ok=false;
   if(side=="BUY") ok=Trade.Buy(volume,_Symbol,0,sl,tp,"RamonCmd:"+command_id);
   else ok=Trade.Sell(volume,_Symbol,0,sl,tp,"RamonCmd:"+command_id);
   if(!ok) { reason="BROKER_REJECTED_"+IntegerToString((int)Trade.ResultRetcode()); return false; }
   ticket_out=Trade.ResultOrder();
   reason="BROKER_ACCEPTED";
   return true;
}

bool LocalExecutionPermitted(string &reason)
{
   if(!EnableCommandExecution)
   {
      reason="LOCAL_EXECUTION_DISABLED";
      return false;
   }
   if(AccountInfoInteger(ACCOUNT_TRADE_MODE)!=ACCOUNT_TRADE_MODE_DEMO)
   {
      reason="DEMO_ACCOUNT_REQUIRED";
      return false;
   }
   if(!TerminalInfoInteger(TERMINAL_TRADE_ALLOWED) ||
      !MQLInfoInteger(MQL_TRADE_ALLOWED))
   {
      reason="TERMINAL_OR_EA_TRADING_PERMISSION_DENIED";
      return false;
   }
   reason="";
   return true;
}


bool ProcessCommand(const string response)
{
   if(StringFind(response,"\"command\":null")>=0 ||
      StringFind(response,"\"command\": null")>=0)
      return true;

   string command=JsonObject(response,"command");
   if(command=="") return false;

   string command_id=JsonString(command,"command_id","");
   string action=JsonString(command,"action","");
   string expires=JsonString(command,"expires_at","");
   string command_instance=JsonString(command,"instance_id","");
   string command_account=JsonString(command,"account_login","");
   string command_symbol=JsonString(command,"symbol","");
   string payload=JsonObject(command,"payload");

   if(command_id=="" || action=="" || payload=="")
      return false;

   string current_account=IntegerToString(
      (long)AccountInfoInteger(ACCOUNT_LOGIN));

   if(command_instance!=InstanceId || command_account!=current_account ||
      command_symbol!=_Symbol)
   {
      Print("RamonCommandEA: command scope mismatch; no trade submitted.");
      return AckCommand(command_id,"REJECTED","COMMAND_SCOPE_MISMATCH");
   }

   if(command_id==LastCommandId)
      return AckCommand(command_id,"RECONCILIATION_REQUIRED",
                        "DUPLICATE_COMMAND_ID_REQUIRES_RECONCILIATION");

   datetime expiry=UtcFromIso(expires);
   if(expiry<=0)
   {
      LastCommandId=command_id;
      return AckCommand(command_id,"REJECTED","INVALID_COMMAND_EXPIRY");
   }
   if(TimeGMT()>expiry)
   {
      LastCommandId=command_id;
      return AckCommand(command_id,"EXPIRED",
                        "COMMAND_EXPIRED_BEFORE_EXECUTION");
   }

   if(!JsonBool(response,"trading_authorization",false) ||
      !JsonBool(command,"execution_authorized",false))
   {
      LastCommandId=command_id;
      return AckCommand(command_id,"REJECTED",
                        "SERVER_AUTHORIZATION_MISSING");
   }

   string guard_reason="";
   if(!LocalExecutionPermitted(guard_reason))
   {
      LastCommandId=command_id;
      return AckCommand(command_id,"REJECTED",guard_reason);
   }

   if(action=="NOOP")
   {
      LastCommandId=command_id;
      return AckCommand(command_id,"EXECUTED","NOOP");
   }

   if(action=="OPEN")
   {
      ulong ticket=0;
      string reason="";
      if(!SafeOpen(payload,command_id,ticket,reason))
      {
         LastCommandId=command_id;
         return AckCommand(command_id,"REJECTED",reason);
      }
      LastCommandId=command_id;
      return AckCommand(command_id,"EXECUTED",reason,ticket);
   }

   if(action=="CLOSE")
   {
      ulong ticket=(ulong)JsonNumber(payload,"position_ticket",0);
      if(ticket==0 || !PositionSelectByTicket(ticket) ||
         PositionGetString(POSITION_SYMBOL)!=command_symbol)
      {
         LastCommandId=command_id;
         return AckCommand(command_id,"REJECTED",
                           "POSITION_NOT_FOUND_OR_SYMBOL_MISMATCH");
      }
      if(!Trade.PositionClose(ticket))
      {
         LastCommandId=command_id;
         return AckCommand(command_id,"FAILED",
             "BROKER_CLOSE_FAILED_"+IntegerToString((int)Trade.ResultRetcode()),
             ticket);
      }
      LastCommandId=command_id;
      return AckCommand(command_id,"EXECUTED","BROKER_CLOSE_ACCEPTED",ticket);
   }

   if(action=="MODIFY_SLTP")
   {
      ulong ticket=(ulong)JsonNumber(payload,"position_ticket",0);
      double sl=JsonNumber(payload,"stop_loss",0);
      double tp=JsonNumber(payload,"take_profit",0);
      if(ticket==0 || !PositionSelectByTicket(ticket) ||
         PositionGetString(POSITION_SYMBOL)!=command_symbol)
      {
         LastCommandId=command_id;
         return AckCommand(command_id,"REJECTED",
                           "POSITION_NOT_FOUND_OR_SYMBOL_MISMATCH");
      }
      if(sl<0 || tp<0 || (sl==0 && tp==0))
      {
         LastCommandId=command_id;
         return AckCommand(command_id,"REJECTED","INVALID_SLTP");
      }
      if(!Trade.PositionModify(ticket,sl,tp))
      {
         LastCommandId=command_id;
         return AckCommand(command_id,"FAILED",
             "BROKER_MODIFY_FAILED_"+IntegerToString((int)Trade.ResultRetcode()),
             ticket);
      }
      LastCommandId=command_id;
      return AckCommand(command_id,"EXECUTED","BROKER_MODIFY_ACCEPTED",ticket);
   }

   LastCommandId=command_id;
   return AckCommand(command_id,"REJECTED","UNKNOWN_ACTION");
}
void PollCommands()
{
   string body="{\"instance_id\":\""+JsonEscape(InstanceId)+"\","
      +"\"account_login\":\""+IntegerToString((long)AccountInfoInteger(ACCOUNT_LOGIN))+"\"}";
   string response;
   if(!PostJson("/commands/next",body,response)) return;
   if(StringFind(response,"\"command\":null")>=0 || StringFind(response,"\"command\": null")>=0) return;
   if(!ProcessCommand(response))
      Print("RamonCommandEA: malformed command response; no action taken.");
}

void OnTimer()
{
   if(Busy) return;
   Busy=true;
   SampleBrokerClock();
   string body=BuildTelemetry();
   if(body!="")
   {
      string response;
      if(!PostJson("/telemetry",body,response))
         Print("RamonCommandEA: telemetry delivery failed.");
   }
   datetime local_now=TimeLocal();
   int decision_interval=MathMax(30,DecisionIntervalSeconds);
   if(LastDecisionAttempt==0 || local_now-LastDecisionAttempt>=decision_interval)
   {
      LastDecisionAttempt=local_now;
      RequestDjangoDecision();
   }
   PollCommands();
   Busy=false;
}

int OnInit()
{
   if(StringLen(InstanceId)<1 || StringLen(InstanceId)>128)
      return INIT_PARAMETERS_INCORRECT;
   if(StringLen(DjangoBearerToken)<32)
   {
      Print("RamonCommandEA: set DjangoBearerToken in Inputs; token must be at least 32 characters.");
      return INIT_PARAMETERS_INCORRECT;
   }
   if(!SampleBrokerClock()) return INIT_FAILED;
   if(!EventSetTimer(MathMax(5,TimerSeconds)))
      return INIT_FAILED;
   Trade.SetAsyncMode(false);
   Print("RamonCommandEA v1.04 initialized in ",EnableCommandExecution ? "DEMO COMMAND MODE" : "TELEMETRY-ONLY MODE",
         "; timer_seconds=",MathMax(5,TimerSeconds),
         "; decision_interval_seconds=",MathMax(30,DecisionIntervalSeconds),
         ". Live accounts are always rejected.");
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   EventKillTimer();
}

void OnTick()
{
   // Network requests run on the timer, not on every tick.
}
