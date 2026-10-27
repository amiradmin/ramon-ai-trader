#property strict
#property version   "1.00"
#property description "Ramon Django command EA: telemetry + guarded demo execution"

#include <Trade/Trade.mqh>

input string ApiBaseUrl = "http://127.0.0.1:8017/api/ea/v1";
input string DjangoBearerToken = "";       // Set locally in MT5; never commit a real token.
input string InstanceId = "ramon-command-ea-1";
input bool EnableCommandExecution = false; // Must remain false until demo validation is complete.
input int TimerSeconds = 15;
input int BarsToSend = 128;
input int RequestTimeoutMs = 5000;
input int MaxSpreadPoints = 80;
input double MaxRiskMoney = 5.0;           // Account-currency risk ceiling per OPEN command.

CTrade Trade;
string LastCommandId = "";
datetime LastTelemetryAttempt = 0;
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

string IsoUtc(datetime stamp)
{
   MqlDateTime d;
   TimeToStruct(stamp,d);
   return StringFormat("%04d-%02d-%02dT%02d:%02d:%02dZ",d.year,d.mon,d.day,d.hour,d.min,d.sec);
}

datetime UtcFromIso(string value)
{
   if(StringLen(value)<19) return 0;
   string normalized=StringSubstr(value,0,19);
   StringReplace(normalized,"-",".");
   StringReplace(normalized,"T"," ");
   return StringToTime(normalized);
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
   return true;
}

string BuildBarsJson()
{
   MqlRates rates[];
   ArraySetAsSeries(rates,true);
   int count=MathMax(1,MathMin(BarsToSend,512));
   int copied=CopyRates(_Symbol,_Period,1,count,rates); // start at 1: omit the still-forming candle
   if(copied<=0) return "[]";
   string out="[";
   for(int i=copied-1;i>=0;i--)
   {
      if(i<copied-1) out+=",";
      datetime utc=rates[i].time-(TimeCurrent()-TimeGMT());
      out+="{\"timestamp\":\""+IsoUtc(utc)+"\","
         +"\"open\":"+DoubleToString(rates[i].open,_Digits)+","
         +"\"high\":"+DoubleToString(rates[i].high,_Digits)+","
         +"\"low\":"+DoubleToString(rates[i].low,_Digits)+","
         +"\"close\":"+DoubleToString(rates[i].close,_Digits)+","
         +"\"volume\":"+DoubleToString((double)rates[i].tick_volume,0)+",\"closed\":true}";
   }
   return out+"]";
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
   string account="{\"currency\":\""+JsonEscape(AccountInfoString(ACCOUNT_CURRENCY))+"\","
      +"\"balance\":"+DoubleToString(AccountInfoDouble(ACCOUNT_BALANCE),2)+","
      +"\"equity\":"+DoubleToString(AccountInfoDouble(ACCOUNT_EQUITY),2)+","
      +"\"margin\":"+DoubleToString(AccountInfoDouble(ACCOUNT_MARGIN),2)+","
      +"\"free_margin\":"+DoubleToString(AccountInfoDouble(ACCOUNT_MARGIN_FREE),2)+"}";
   return "{\"protocol_version\":\"ramon-ea-command-v1\","
      +"\"instance_id\":\""+JsonEscape(InstanceId)+"\","
      +"\"account_login\":\""+IntegerToString((long)AccountInfoInteger(ACCOUNT_LOGIN))+"\","
      +"\"symbol\":\""+JsonEscape(_Symbol)+"\",\"timeframe\":\""+TfName(_Period)+"\","
      +"\"quote\":{\"bid\":"+DoubleToString(tick.bid,_Digits)+",\"ask\":"+DoubleToString(tick.ask,_Digits)+",\"point\":"+DoubleToString(point,_Digits)+"},"
      +"\"quote_time\":\""+IsoUtc(tick.time-(TimeCurrent()-TimeGMT()))+"\","
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

bool ProcessCommand(const string response)
{
   if(StringFind(response,"\"command\":null")>=0 || StringFind(response,"\"command\": null")>=0)
      return true;
   string command_id=JsonString(response,"command_id","");
   string action=JsonString(response,"action","");
   string expires=JsonString(response,"expires_at","");
   if(command_id=="" || action=="") return false;
   if(command_id==LastCommandId) return AckCommand(command_id,"RECONCILIATION_REQUIRED","DUPLICATE_COMMAND_ID_REQUIRES_RECONCILIATION");
   datetime expiry=UtcFromIso(expires);
   if(expiry>0 && TimeGMT()>expiry)
   {
      LastCommandId=command_id;
      return AckCommand(command_id,"EXPIRED","COMMAND_EXPIRED_BEFORE_EXECUTION");
   }

   LastCommandId=command_id;
   if(!JsonBool(response,"trading_authorization",false) || !JsonBool(response,"execution_authorized",false))
      return AckCommand(command_id,"REJECTED","SERVER_AUTHORIZATION_MISSING");
   if(!EnableCommandExecution)
      return AckCommand(command_id,"REJECTED","LOCAL_EXECUTION_DISABLED");

   if(action=="NOOP") return AckCommand(command_id,"EXECUTED","NOOP");
   if(action=="OPEN")
   {
      ulong ticket=0;
      string reason="";
      if(!SafeOpen(response,command_id,ticket,reason))
         return AckCommand(command_id,"REJECTED",reason);
      return AckCommand(command_id,"EXECUTED",reason,ticket);
   }
   if(action=="CLOSE")
   {
      ulong ticket=(ulong)JsonNumber(response,"position_ticket",0);
      if(ticket==0 || !PositionSelectByTicket(ticket) || PositionGetString(POSITION_SYMBOL)!=_Symbol)
         return AckCommand(command_id,"REJECTED","POSITION_NOT_FOUND_OR_SYMBOL_MISMATCH");
      if(!Trade.PositionClose(ticket))
         return AckCommand(command_id,"FAILED","BROKER_CLOSE_FAILED_"+IntegerToString((int)Trade.ResultRetcode()),ticket);
      return AckCommand(command_id,"EXECUTED","BROKER_CLOSE_ACCEPTED",ticket);
   }
   if(action=="MODIFY_SLTP")
   {
      ulong ticket=(ulong)JsonNumber(response,"position_ticket",0);
      double sl=JsonNumber(response,"stop_loss",0);
      double tp=JsonNumber(response,"take_profit",0);
      if(ticket==0 || !PositionSelectByTicket(ticket) || PositionGetString(POSITION_SYMBOL)!=_Symbol)
         return AckCommand(command_id,"REJECTED","POSITION_NOT_FOUND_OR_SYMBOL_MISMATCH");
      if(sl<0 || tp<0 || (sl==0 && tp==0))
         return AckCommand(command_id,"REJECTED","INVALID_SLTP");
      if(!Trade.PositionModify(ticket,sl,tp))
         return AckCommand(command_id,"FAILED","BROKER_MODIFY_FAILED_"+IntegerToString((int)Trade.ResultRetcode()),ticket);
      return AckCommand(command_id,"EXECUTED","BROKER_MODIFY_ACCEPTED",ticket);
   }
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
   string body=BuildTelemetry();
   if(body!="")
   {
      string response;
      if(!PostJson("/telemetry",body,response))
         Print("RamonCommandEA: telemetry delivery failed.");
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
   if(!EventSetTimer(MathMax(5,TimerSeconds)))
      return INIT_FAILED;
   Trade.SetAsyncMode(false);
   Print("RamonCommandEA initialized in ",EnableCommandExecution ? "DEMO COMMAND MODE" : "TELEMETRY-ONLY MODE",
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
