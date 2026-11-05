#property strict
#property version   "1.07"
#property description "RamonCommandEA v1.07: Django telemetry, learning outcomes + guarded execution"
#define RAMON_COMMAND_EA_VERSION "1.07"

#include <Trade/Trade.mqh>

input string ApiBaseUrl = "http://127.0.0.1:8017/api/ea/v1";
input string DjangoBearerToken = "";       // Set locally in MT5; never commit a real token.
input string InstanceId = "ramon-command-ea-1";
input int TimerSeconds = 15;
input int DecisionIntervalSeconds = 30; // Django decision requests, independent of telemetry polling.
input int BarsToSend = 128;
input int RequestTimeoutMs = 5000;

CTrade Trade;
string LastCommandId = "";
datetime LastTelemetryAttempt = 0;
datetime LastDecisionAttempt = 0;
bool Busy = false;

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


bool GetServerUtcOffsetSeconds(long &offset_seconds)
{
   datetime server_before=TimeTradeServer();
   datetime utc_now=TimeGMT();
   datetime server_after=TimeTradeServer();
   if(server_before<=0 || utc_now<=0 || server_after<=0)
      return false;

   long server_midpoint=(long)server_before+
      (((long)server_after-(long)server_before)/2);
   long raw_offset=server_midpoint-(long)utc_now;
   // Broker timezone offsets are minute-granular; discard clock sampling jitter.
   offset_seconds=(long)MathRound((double)raw_offset/60.0)*60;
   return true;
}

datetime NormalizeM15Utc(datetime utc_stamp)
{
   // M15 candle opens are 900-second boundaries. Remove residual sub-minute
   // clock skew after applying the broker UTC offset.
   return (datetime)((long)MathRound((double)((long)utc_stamp)/900.0)*900);
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
   int copied=CopyRates(_Symbol,_Period,1,count,rates); // start at 1: omit the still-forming candle
   if(copied<=0) return "[]";
   long server_utc_offset=0;
   if(!GetServerUtcOffsetSeconds(server_utc_offset)) return "[]";
   string out="[";
   for(int i=copied-1;i>=0;i--)
   {
      if(i<copied-1) out+=",";
      datetime utc=(datetime)((long)rates[i].time-server_utc_offset);
      if(_Period==PERIOD_M15) utc=NormalizeM15Utc(utc);
      out+="{\"timestamp\":\""+IsoUtc(utc)+"\","
         +"\"open\":"+DoubleToString(rates[i].open,_Digits)+","
         +"\"high\":"+DoubleToString(rates[i].high,_Digits)+","
         +"\"low\":"+DoubleToString(rates[i].low,_Digits)+","
         +"\"close\":"+DoubleToString(rates[i].close,_Digits)+","
         +"\"volume\":"+DoubleToString((double)rates[i].tick_volume,0)+",\"closed\":true}";
   }
   return out+"]";
}


string BuildDecisionBarsJson()
{
   if(_Period!=PERIOD_M15) return "[]";
   MqlRates rates[];
   ArraySetAsSeries(rates,true);
   int count=MathMax(128,MathMin(BarsToSend,512));
   int copied=CopyRates(_Symbol,PERIOD_M15,1,count,rates); // completed candles only
   if(copied<128) return "[]";

   // MqlRates timestamps use broker-server time. Use the estimated current
   // server/GMT offset, not TimeCurrent(), which can freeze at the last quote.
   datetime server_now=TimeTradeServer();
   datetime utc_now=TimeGMT();
   long server_utc_offset=0;
   if(server_now<=0 || utc_now<=0 ||
      !GetServerUtcOffsetSeconds(server_utc_offset)) return "[]";
   Print("RAMON TIME DEBUG: server_now=",TimeToString(server_now,TIME_DATE|TIME_SECONDS),
         " utc_now=",TimeToString(utc_now,TIME_DATE|TIME_SECONDS),
         " offset_seconds=",server_utc_offset,
         " newest_raw_bar=",TimeToString(rates[0].time,TIME_DATE|TIME_SECONDS),
         " newest_converted_bar=",
         TimeToString((datetime)((long)rates[0].time-server_utc_offset),TIME_DATE|TIME_SECONDS));


   string out="[";
   for(int i=copied-1;i>=0;i--)
   {
      if(i<copied-1) out+=",";
      datetime utc=(datetime)((long)rates[i].time-server_utc_offset);
      utc=NormalizeM15Utc(utc);
      out+="{\"time\":"+IntegerToString((long)utc)+","
         +"\"open\":"+DoubleToString(rates[i].open,_Digits)+","
         +"\"high\":"+DoubleToString(rates[i].high,_Digits)+","
         +"\"low\":"+DoubleToString(rates[i].low,_Digits)+","
         +"\"close\":"+DoubleToString(rates[i].close,_Digits)+"}";
   }
   return out+"]";
}

bool CalculateBrokerDayOpeningBalance(const datetime server_now,const double balance_before,
                                       double &opening_balance,int &deal_count)
{
   opening_balance=0.0;
   deal_count=0;

   if(server_now<=0 || !MathIsValidNumber(balance_before) || balance_before<=0.0)
      return false;

   MqlDateTime broker_day_parts;
   if(!TimeToStruct(server_now,broker_day_parts)) return false;
   broker_day_parts.hour=0;
   broker_day_parts.min=0;
   broker_day_parts.sec=0;
   datetime broker_day_start=StructToTime(broker_day_parts);
   if(broker_day_start<=0 || broker_day_start>server_now) return false;

   ResetLastError();
   if(!HistorySelect(broker_day_start,server_now)) return false;

   int total=HistoryDealsTotal();
   if(total<0) return false;

   double net_cashflow=0.0;
   for(int i=0;i<total;i++)
   {
      ulong deal=HistoryDealGetTicket(i);
      if(deal==0) return false;

      long deal_type=0;
      if(!HistoryDealGetInteger(deal,DEAL_TYPE,deal_type)) return false;
      // CREDIT changes the separate account-credit ledger, not ACCOUNT_BALANCE.
      if((ENUM_DEAL_TYPE)deal_type==DEAL_TYPE_CREDIT) continue;

      double profit=0.0;
      double swap=0.0;
      double commission=0.0;
      double fee=0.0;
      if(!HistoryDealGetDouble(deal,DEAL_PROFIT,profit) ||
         !HistoryDealGetDouble(deal,DEAL_SWAP,swap) ||
         !HistoryDealGetDouble(deal,DEAL_COMMISSION,commission) ||
         !HistoryDealGetDouble(deal,DEAL_FEE,fee))
         return false;

      net_cashflow+=profit+swap+commission+fee;
      if(!MathIsValidNumber(net_cashflow)) return false;
   }

   double current_balance=AccountInfoDouble(ACCOUNT_BALANCE);
   // Avoid pairing a history snapshot with a balance changed during the read.
   if(!MathIsValidNumber(current_balance) ||
      MathAbs(current_balance-balance_before)>0.005)
      return false;

   opening_balance=current_balance-net_cashflow;
   if(!MathIsValidNumber(opening_balance) || opening_balance<=0.0)
      return false;

   deal_count=total;
   return true;
}

void RequestDjangoDecision()
{
   Print("RamonCommandEA v1.05: Django decision poll; symbol=", _Symbol,
         " timeframe=", TfName(_Period));
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
   if(point<=0) return;

   string body="{\"protocol_version\":\"ramon-django-decision-v1\","
      +"\"instance_id\":\""+JsonEscape(InstanceId)+"\","
      +"\"account_login\":\""+IntegerToString((long)AccountInfoInteger(ACCOUNT_LOGIN))+"\","
      +"\"server_name\":\""+JsonEscape(AccountInfoString(ACCOUNT_SERVER))+"\","
      +"\"symbol\":\""+JsonEscape(_Symbol)+"\",\"timeframe\":\"M15\","
      +"\"bid\":"+DoubleToString(tick.bid,_Digits)+","
      +"\"ask\":"+DoubleToString(tick.ask,_Digits)+","
      +"\"point\":"+DoubleToString(point,10)+","
      +"\"bars\":"+bars+"}";

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
         +"\"profit\":"+DoubleToString(PositionGetDouble(POSITION_PROFIT),2)+","
         +"\"tick_size\":"+DoubleToString(SymbolInfoDouble(PositionGetString(POSITION_SYMBOL),SYMBOL_TRADE_TICK_SIZE),10)+","
         +"\"tick_value\":"+DoubleToString(SymbolInfoDouble(PositionGetString(POSITION_SYMBOL),SYMBOL_TRADE_TICK_VALUE_LOSS),8)+"}";
   }
   return out+"]";
}

string BuildTelemetry()
{
   MqlTick tick;
   if(!SymbolInfoTick(_Symbol,tick) || tick.bid<=0 || tick.ask<tick.bid) return "";
   double point=SymbolInfoDouble(_Symbol,SYMBOL_POINT);
   if(point<=0) return "";
   double baseline_balance_snapshot=AccountInfoDouble(ACCOUNT_BALANCE);
   datetime server_now=TimeTradeServer();
   long server_utc_offset=0;
   if(server_now<=0 || !GetServerUtcOffsetSeconds(server_utc_offset)) return "";
   // The risk baseline key uses the broker's calendar day, not local PC or UTC date.
   MqlDateTime broker_day_parts;
   TimeToStruct(server_now,broker_day_parts);
   string broker_day_text=StringFormat("%04d-%02d-%02d",
      broker_day_parts.year,broker_day_parts.mon,broker_day_parts.day);
   double broker_day_opening_balance=0.0;
   int broker_day_deal_count=0;
   bool broker_day_history_complete=CalculateBrokerDayOpeningBalance(
      server_now,baseline_balance_snapshot,
      broker_day_opening_balance,broker_day_deal_count);
   string symbol_info="{\"tick_size\":"+DoubleToString(SymbolInfoDouble(_Symbol,SYMBOL_TRADE_TICK_SIZE),10)+","
      +"\"tick_value\":"+DoubleToString(SymbolInfoDouble(_Symbol,SYMBOL_TRADE_TICK_VALUE),8)+","
      +"\"volume_min\":"+DoubleToString(SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_MIN),8)+","
      +"\"volume_max\":"+DoubleToString(SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_MAX),8)+","
      +"\"volume_step\":"+DoubleToString(SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_STEP),8)+","
      +"\"stops_level_points\":"+DoubleToString((double)SymbolInfoInteger(_Symbol,SYMBOL_TRADE_STOPS_LEVEL),2)+"}";
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
      +"\"broker_day_history_complete\":"+(broker_day_history_complete ? "true":"false")+","
      +"\"broker_day_opening_balance\":"+DoubleToString(broker_day_opening_balance,2)+","
      +"\"broker_day_deal_count\":"+IntegerToString(broker_day_deal_count)+","
      +"\"symbol\":\""+JsonEscape(_Symbol)+"\",\"timeframe\":\""+TfName(_Period)+"\","
      +"\"quote\":{\"bid\":"+DoubleToString(tick.bid,_Digits)+",\"ask\":"+DoubleToString(tick.ask,_Digits)+",\"point\":"+DoubleToString(point,_Digits)+"},"
      +"\"quote_time\":\""+IsoUtc((datetime)((long)tick.time-server_utc_offset))+"\","
      +"\"terminal_trade_allowed\":"+((bool)TerminalInfoInteger(TERMINAL_TRADE_ALLOWED) && (bool)MQLInfoInteger(MQL_TRADE_ALLOWED) ? "true":"false")+","
      +"\"account\":"+account+",\"symbol_info\":"+symbol_info+",\"positions\":"+BuildPositionsJson()+","
      +"\"last_command_id\":\""+JsonEscape(LastCommandId)+"\",\"bars\":"+BuildBarsJson()+"}";
}



// -----------------------------------------------------------------------------
// Assisted-learning outcome delivery
// -----------------------------------------------------------------------------

string LearningCursorName()
{
   return "RamonLearn."+
      IntegerToString((long)AccountInfoInteger(ACCOUNT_LOGIN))+"."+
      StringSubstr(InstanceId,0,12);
}

string LearningTicketCursorName()
{
   return LearningCursorName()+".T";
}

bool IsPositionIdentifierOpen(const ulong position_id)
{
   for(int i=0;i<PositionsTotal();i++)
   {
      ulong ticket=PositionGetTicket(i);
      if(ticket==0) continue;
      if((ulong)PositionGetInteger(POSITION_IDENTIFIER)==position_id)
         return true;
   }
   return false;
}

bool ContainsPositionId(const ulong &position_ids[],const ulong position_id)
{
   for(int i=0;i<ArraySize(position_ids);i++)
      if(position_ids[i]==position_id) return true;
   return false;
}

void SaveLearningCursor(const string cursor_name,
                        const string ticket_name,
                        const long deal_time,
                        const ulong deal_ticket)
{
   GlobalVariableSet(cursor_name,(double)deal_time);
   GlobalVariableSet(ticket_name,(double)deal_ticket);
   GlobalVariablesFlush();
}

string DealReasonName(const long reason)
{
   if(reason==DEAL_REASON_TP) return "TP";
   if(reason==DEAL_REASON_SL) return "SL";
   if(reason==DEAL_REASON_SO) return "STOP_OUT";
   if(reason==DEAL_REASON_EXPERT) return "EXPERT";
   if(reason==DEAL_REASON_CLIENT) return "CLIENT";
   if(reason==DEAL_REASON_MOBILE) return "MOBILE";
   if(reason==DEAL_REASON_WEB) return "WEB";
   if(reason==DEAL_REASON_ROLLOVER) return "ROLLOVER";
   if(reason==DEAL_REASON_VMARGIN) return "MARGIN";
   if(reason==DEAL_REASON_SPLIT) return "SPLIT";
   if(reason==DEAL_REASON_CORPORATE_ACTION) return "CORPORATE_ACTION";
   return "UNKNOWN";
}

string CommandIdFromComment(const string comment)
{
   int prefix=StringFind(comment,"RamonCmd:");
   if(prefix<0) return "";
   return StringSubstr(comment,prefix+9);
}

bool BuildLearningOutcomeJson(const ulong position_id,
                              const long server_utc_offset,
                              string &body,
                              string &trade_id)
{
   if(!HistorySelectByPosition(position_id))
      return false;

   int total=HistoryDealsTotal();
   if(total<=0) return false;

   double net_pnl=0.0;
   double total_commission=0.0;
   double total_swap=0.0;
   double opening_volume=0.0;
   double weighted_entry_price=0.0;
   datetime opened_server=0;
   datetime closed_server=0;
   ulong opening_order=0;
   string symbol="";
   string side="";
   string exit_reason="UNKNOWN";
   string command_id="";

   for(int i=0;i<total;i++)
   {
      ulong deal=HistoryDealGetTicket(i);
      if(deal==0) continue;

      long entry=HistoryDealGetInteger(deal,DEAL_ENTRY);
      long deal_time=HistoryDealGetInteger(deal,DEAL_TIME);
      long deal_type=HistoryDealGetInteger(deal,DEAL_TYPE);
      string deal_symbol=HistoryDealGetString(deal,DEAL_SYMBOL);
      double profit=HistoryDealGetDouble(deal,DEAL_PROFIT);
      double swap=HistoryDealGetDouble(deal,DEAL_SWAP);
      double commission=HistoryDealGetDouble(deal,DEAL_COMMISSION);
      double fee=HistoryDealGetDouble(deal,DEAL_FEE);

      // Net PnL includes all position-linked broker costs, including entry fees.
      net_pnl+=profit+swap+commission+fee;
      total_commission+=commission+fee;
      total_swap+=swap;

      if(entry==DEAL_ENTRY_IN)
      {
         double deal_volume=HistoryDealGetDouble(deal,DEAL_VOLUME);
         double deal_price=HistoryDealGetDouble(deal,DEAL_PRICE);
         if(deal_volume>0.0 && deal_price>0.0)
         {
            opening_volume+=deal_volume;
            weighted_entry_price+=deal_volume*deal_price;
         }
         if(opened_server==0 || deal_time<opened_server)
         {
            opened_server=(datetime)deal_time;
            opening_order=(ulong)HistoryDealGetInteger(deal,DEAL_ORDER);
            symbol=deal_symbol;
            if(deal_type==DEAL_TYPE_BUY) side="BUY";
            else if(deal_type==DEAL_TYPE_SELL) side="SELL";

            string comment=HistoryDealGetString(deal,DEAL_COMMENT);
            command_id=CommandIdFromComment(comment);
            if(command_id=="" && opening_order>0 && HistoryOrderSelect(opening_order))
               command_id=CommandIdFromComment(
                  HistoryOrderGetString(opening_order,ORDER_COMMENT));
         }
      }

      if(entry==DEAL_ENTRY_OUT || entry==DEAL_ENTRY_OUT_BY)
      {
         if(deal_time>=closed_server)
         {
            closed_server=(datetime)deal_time;
            exit_reason=DealReasonName(
               HistoryDealGetInteger(deal,DEAL_REASON));
         }
      }
   }

   // The current Ramon account is hedging. Netting reversal (DEAL_ENTRY_INOUT)
   // is intentionally not merged into a completed position outcome.
   if(opened_server<=0 || closed_server<=0 || closed_server<opened_server ||
      symbol=="" || (side!="BUY" && side!="SELL"))
      return false;

   long login=(long)AccountInfoInteger(ACCOUNT_LOGIN);
   string server_key=StringSubstr(AccountInfoString(ACCOUNT_SERVER),0,64);
   trade_id=IntegerToString(login)+":"+server_key+":"+
      IntegerToString((long)position_id);

   datetime opened_utc=(datetime)((long)opened_server-server_utc_offset);
   datetime closed_utc=(datetime)((long)closed_server-server_utc_offset);

   body="{\"trade_id\":\""+JsonEscape(trade_id)+"\","
      +"\"position_id\":\""+IntegerToString((long)position_id)+"\","
      +"\"instance_id\":\""+JsonEscape(InstanceId)+"\","
      +"\"account_login\":\""+IntegerToString(login)+"\","
      +"\"symbol\":\""+JsonEscape(symbol)+"\","
      +"\"side\":\""+side+"\","
      +"\"exit_reason\":\""+exit_reason+"\","
      +"\"realized_pnl\":"+DoubleToString(net_pnl,8)+","
      +"\"commission\":"+DoubleToString(total_commission,8)+","
      +"\"swap\":"+DoubleToString(total_swap,8)+","
      +"\"opened_at\":\""+IsoUtc(opened_utc)+"\","
      +"\"closed_at\":\""+IsoUtc(closed_utc)+"\"";

   // Send the volume-weighted broker fill price; Django compares it with the
   // persisted decision quote and calculates adverse slippage in points.
   if(opening_volume>0.0)
      body+=",\"entry_price\":"+DoubleToString(weighted_entry_price/opening_volume,10);

   if(opening_order>0)
      body+=",\"broker_ticket\":\""+IntegerToString((long)opening_order)+"\"";

   // Broker comments can truncate command IDs. The opening order ticket is
   // the authoritative fallback used by Django to resolve the command.
   if(command_id!="")
      body+=",\"command_id\":\""+JsonEscape(command_id)+"\"";
   body+="}";
   return true;
}

void PollLearningOutcomes()
{
   datetime server_now=TimeTradeServer();
   long server_utc_offset=0;
   if(server_now<=0 || !GetServerUtcOffsetSeconds(server_utc_offset))
      return;

   string cursor_name=LearningCursorName();
   string ticket_name=LearningTicketCursorName();

   if(!GlobalVariableCheck(cursor_name))
      GlobalVariableSet(cursor_name,(double)((long)server_now-90*86400));
   if(!GlobalVariableCheck(ticket_name))
      GlobalVariableSet(ticket_name,0.0);

   long cursor_time=(long)GlobalVariableGet(cursor_name);
   ulong cursor_ticket=(ulong)GlobalVariableGet(ticket_name);
   datetime scan_from=(datetime)MathMax(0,cursor_time-60);

   if(!HistorySelect(scan_from,server_now))
   {
      Print("Ramon Learning: broker history selection failed; cursor retained.");
      return;
   }

   int total=HistoryDealsTotal();
   if(total<=0) return;

   // Snapshot the selected deal list before HistorySelectByPosition changes it.
   ulong deal_tickets[];
   ulong position_ids[];
   long deal_times[];
   long deal_entries[];
   ArrayResize(deal_tickets,total);
   ArrayResize(position_ids,total);
   ArrayResize(deal_times,total);
   ArrayResize(deal_entries,total);

   for(int i=0;i<total;i++)
   {
      ulong deal=HistoryDealGetTicket(i);
      deal_tickets[i]=deal;
      position_ids[i]=(ulong)HistoryDealGetInteger(deal,DEAL_POSITION_ID);
      deal_times[i]=HistoryDealGetInteger(deal,DEAL_TIME);
      deal_entries[i]=HistoryDealGetInteger(deal,DEAL_ENTRY);
   }

   ulong delivered_positions[];
   ArrayResize(delivered_positions,0);

   for(int i=0;i<total;i++)
   {
      ulong deal=deal_tickets[i];
      long deal_time=deal_times[i];

      if(deal==0 || deal_time<cursor_time ||
         (deal_time==cursor_time && deal<=cursor_ticket))
         continue;

      long entry=deal_entries[i];
      ulong position_id=position_ids[i];

      if((entry==DEAL_ENTRY_OUT || entry==DEAL_ENTRY_OUT_BY) &&
         position_id>0 && !IsPositionIdentifierOpen(position_id) &&
         !ContainsPositionId(delivered_positions,position_id))
      {
         string body="";
         string trade_id="";
         if(!BuildLearningOutcomeJson(position_id,server_utc_offset,body,trade_id))
         {
            Print("Ramon Learning: could not build completed-position outcome; cursor retained. position_id=",
                  position_id);
            return;
         }

         string response="";
         if(!PostJson("/learning/outcomes",body,response))
         {
            Print("Ramon Learning: outcome delivery failed; will retry. trade_id=",
                  trade_id);
            return;
         }

         int count=ArraySize(delivered_positions);
         ArrayResize(delivered_positions,count+1);
         delivered_positions[count]=position_id;
         Print("Ramon Learning: completed position accepted by Django. trade_id=",
               trade_id," response=",response);
      }

      // Persist only after a successful POST or a deal that needs no outcome.
      // A failed POST leaves the cursor before that closing deal for retry.
      SaveLearningCursor(cursor_name,ticket_name,deal_time,deal);
   }
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

void SafeOpen(const string command_json,const string command_id,
              ulong &ticket_out,string &reason,string &ack_state)
{
   // Django owns strategy and risk. This function verifies broker submission outcome.
   string side=JsonString(command_json,"side","");
   double volume=JsonNumber(command_json,"volume",0);
   double sl=JsonNumber(command_json,"stop_loss",0);
   double tp=JsonNumber(command_json,"take_profit",0);
   if((side!="BUY" && side!="SELL") || volume<=0 || sl<=0 || tp<=0)
   {
      reason="MALFORMED_DJANGO_COMMAND";
      ack_state="REJECTED";
      return;
   }

   bool submitted=(side=="BUY")
      ? Trade.Buy(volume,_Symbol,0,sl,tp,"RamonCmd:"+command_id)
      : Trade.Sell(volume,_Symbol,0,sl,tp,"RamonCmd:"+command_id);
   uint retcode=(uint)Trade.ResultRetcode();
   string retcode_text=IntegerToString((int)retcode)+"_"+Trade.ResultRetcodeDescription();

   if(!submitted)
   {
      reason="BROKER_REJECTED_"+retcode_text;
      ack_state="REJECTED";
      return;
   }

   ticket_out=Trade.ResultOrder();
   if(ticket_out==0)
      ticket_out=Trade.ResultDeal();

   if(retcode==TRADE_RETCODE_DONE)
   {
      if(ticket_out==0)
      {
         reason="BROKER_FILL_WITHOUT_IDENTIFIER_"+retcode_text;
         ack_state="RECONCILIATION_REQUIRED";
         return;
      }
      reason="BROKER_FILLED_"+retcode_text;
      ack_state="EXECUTED";
      return;
   }

   // A partial fill or merely placed order is not reported as fully executed.
   // Do not retry: reconcile the broker order/deal/position first.
   if(retcode==TRADE_RETCODE_DONE_PARTIAL || retcode==TRADE_RETCODE_PLACED)
   {
      reason="BROKER_RESULT_REQUIRES_RECONCILIATION_"+retcode_text;
      ack_state="RECONCILIATION_REQUIRED";
      return;
   }

   reason="BROKER_NOT_FILLED_"+retcode_text;
   ack_state="REJECTED";
}

bool ProcessCommand(const string response)
{
   if(StringFind(response,"\\"command\\":null")>=0 ||
      StringFind(response,"\\"command\\": null")>=0)
      return true;

   string command=JsonObject(response,"command");
   if(command=="") return false;
   string command_id=JsonString(command,"command_id","");
   string action=JsonString(command,"action","");
   string payload=JsonObject(command,"payload");
   if(command_id=="" || action=="" || payload=="")
      return false;

   // Django has completed every authorization, account, market, strategy,
   // expiry, duplicate and risk check before delivering this command.
   // EA only dispatches the authorized instruction and reports broker outcome.
   if(action=="NOOP")
      return AckCommand(command_id,"EXECUTED","NOOP");

   if(action=="OPEN")
   {
      ulong ticket=0;
      string reason="";
      string ack_state="REJECTED";
      SafeOpen(payload,command_id,ticket,reason,ack_state);
      return AckCommand(command_id,ack_state,reason,ticket);
   }

   if(action=="CLOSE")
   {
      ulong ticket=(ulong)JsonNumber(payload,"position_ticket",0);
      bool submitted=Trade.PositionClose(ticket);
      uint retcode=(uint)Trade.ResultRetcode();
      string detail=IntegerToString((int)retcode)+"_"+Trade.ResultRetcodeDescription();
      return AckCommand(command_id,submitted && retcode==TRADE_RETCODE_DONE ? "EXECUTED":"FAILED",
                        "BROKER_CLOSE_"+detail,ticket);
   }

   if(action=="MODIFY_SLTP")
   {
      ulong ticket=(ulong)JsonNumber(payload,"position_ticket",0);
      double sl=JsonNumber(payload,"stop_loss",0);
      double tp=JsonNumber(payload,"take_profit",0);
      bool submitted=Trade.PositionModify(ticket,sl,tp);
      uint retcode=(uint)Trade.ResultRetcode();
      string detail=IntegerToString((int)retcode)+"_"+Trade.ResultRetcodeDescription();
      return AckCommand(command_id,submitted && retcode==TRADE_RETCODE_DONE ? "EXECUTED":"FAILED",
                        "BROKER_MODIFY_"+detail,ticket);
   }

   return AckCommand(command_id,"FAILED","UNSUPPORTED_DJANGO_ACTION");
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
   PollLearningOutcomes();
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
   if(!EventSetTimer(MathMax(5,TimerSeconds)))
      return INIT_FAILED;
   Trade.SetAsyncMode(false);
   Trade.SetTypeFillingBySymbol(_Symbol);
   Print("RamonCommandEA v1.07 initialized as passive Django bridge",
         "; timer_seconds=",MathMax(5,TimerSeconds),
         "; decision_interval_seconds=",MathMax(30,DecisionIntervalSeconds),
         ". Strategy and risk policy are server-owned.");
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
