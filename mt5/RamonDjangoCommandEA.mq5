//+------------------------------------------------------------------+
//| RamonDjangoCommandEA.mq5                                         |
//| Phase 1: telemetry-only bridge prototype.                         |
//| Trading and command polling are intentionally NOT implemented.    |
//+------------------------------------------------------------------+
#property strict
#property version   "0.10.0"
#property description "Ramon Django telemetry-only EA prototype; never places orders."

input string DjangoBaseUrl = "http://127.0.0.1:8017/api/ea/v1";
input string DjangoBearerToken = ""; // Configure locally; never commit a token.
input string InstanceId = "";        // Set a unique stable ID per terminal/account.
input int    RequestTimeoutMs = 3000;
input int    TimerSeconds = 15;
input bool   EnableTelemetry = true;

// These are hard safety locks in this prototype. There is no order code.
const bool COMMAND_POLLING_IMPLEMENTED = false;
const bool ORDER_EXECUTION_IMPLEMENTED = false;

datetime g_last_bar_time = 0;
datetime g_last_heartbeat = 0;
ulong g_event_sequence = 0;

string JsonEscape(const string value)
{
   string out = value;
   StringReplace(out, "\\", "\\\\");
   StringReplace(out, "\"", "\\\"");
   StringReplace(out, "\r", "");
   StringReplace(out, "\n", "\\n");
   StringReplace(out, "\t", "\\t");
   return out;
}

string AccountLoginText()
{
   return IntegerToString((long)AccountInfoInteger(ACCOUNT_LOGIN));
}

string IsoUtc(const datetime stamp)
{
   MqlDateTime parts;
   TimeToStruct(stamp, parts);
   return StringFormat("%04d-%02d-%02dT%02d:%02d:%02dZ",
                       parts.year, parts.mon, parts.day,
                       parts.hour, parts.min, parts.sec);
}

string MakeEventId(const string kind, const datetime stamp)
{
   g_event_sequence++;
   return InstanceId + "-" + kind + "-" + IntegerToString((long)stamp)
          + "-" + IntegerToString((long)g_event_sequence);
}

bool PostJson(const string endpoint, const string payload, string &reply, int &http_code)
{
   reply = "";
   http_code = 0;
   if(StringLen(DjangoBearerToken) < 32)
   {
      Print("Ramon Django telemetry skipped: configure a bearer token of at least 32 characters.");
      return false;
   }

   char body[];
   char result[];
   string response_headers;
   string headers = "Content-Type: application/json\r\nAuthorization: Bearer "
                    + DjangoBearerToken + "\r\n";
   int copied = StringToCharArray(payload, body, 0, WHOLE_ARRAY, CP_UTF8);
   if(copied <= 1)
      return false;
   ArrayResize(body, copied - 1); // Remove the terminating NUL.
   ResetLastError();
   http_code = WebRequest("POST", DjangoBaseUrl + endpoint, headers,
                          RequestTimeoutMs, body, result, response_headers);
   if(http_code == -1)
   {
      PrintFormat("Ramon Django request failed: endpoint=%s error=%d. Check MT5 WebRequest allowlist.",
                  endpoint, GetLastError());
      return false;
   }
   reply = CharArrayToString(result, 0, WHOLE_ARRAY, CP_UTF8);
   return (http_code >= 200 && http_code < 300);
}

bool SendHeartbeat()
{
   MqlTick tick;
   bool have_tick = SymbolInfoTick(_Symbol, tick);
   string status = have_tick ? "TELEMETRY_ONLY" : "NO_CURRENT_TICK";
   string payload = "{"
      + "\"instance_id\":\"" + JsonEscape(InstanceId) + "\","
      + "\"account_login\":\"" + AccountLoginText() + "\","
      + "\"server_name\":\"" + JsonEscape(AccountInfoString(ACCOUNT_SERVER)) + "\","
      + "\"symbol\":\"" + JsonEscape(_Symbol) + "\","
      + "\"ea_version\":\"0.10.0\","
      + "\"status\":\"" + status + "\","
      + "\"terminal_connected\":" + (((bool)TerminalInfoInteger(TERMINAL_CONNECTED)) ? "true" : "false")
      + ",\"trade_allowed\":false,"
      + "\"telemetry_only\":true,"
      + "\"command_polling_enabled\":false,"
      + "\"order_execution_enabled\":false,"
      + "\"quote_time\":" + IntegerToString(have_tick ? (long)tick.time : 0)
      + ",\"bid\":" + DoubleToString(have_tick ? tick.bid : 0.0, _Digits)
      + ",\"ask\":" + DoubleToString(have_tick ? tick.ask : 0.0, _Digits)
      + ",\"balance\":" + DoubleToString(AccountInfoDouble(ACCOUNT_BALANCE), 2)
      + ",\"equity\":" + DoubleToString(AccountInfoDouble(ACCOUNT_EQUITY), 2)
      + ",\"account_currency\":\"" + JsonEscape(AccountInfoString(ACCOUNT_CURRENCY)) + "\""
      + "}";

   string reply;
   int code;
   bool ok = PostJson("/heartbeat", payload, reply, code);
   if(!ok)
      PrintFormat("Ramon Django heartbeat failed: HTTP=%d response=%s", code, reply);
   else
      g_last_heartbeat = TimeCurrent();
   return ok;
}

bool SendMarketSnapshot()
{
   MqlTick tick;
   if(!SymbolInfoTick(_Symbol, tick) || tick.bid <= 0.0 || tick.ask < tick.bid)
      return false;

   MqlRates bars[2];
   if(CopyRates(_Symbol, PERIOD_M15, 1, 1, bars) != 1)
   {
      Print("Ramon Django telemetry waiting for a completed M15 candle.");
      return false;
   }

   datetime bar_time = bars[0].time;
   string event_id = MakeEventId("SNAPSHOT", (datetime)(GetTickCount64() / 1000));
   string payload = "{"
      + "\"instance_id\":\"" + JsonEscape(InstanceId) + "\","
      + "\"event_id\":\"" + JsonEscape(event_id) + "\","
      + "\"event_type\":\"MARKET_SNAPSHOT\","
      + "\"occurred_at\":\"" + IsoUtc(TimeGMT()) + "\","
      + "\"symbol\":\"" + JsonEscape(_Symbol) + "\","
      + "\"timeframe\":\"M15\","
      + "\"quote_time\":" + IntegerToString((long)tick.time) + ","
      + "\"bid\":" + DoubleToString(tick.bid, _Digits) + ","
      + "\"ask\":" + DoubleToString(tick.ask, _Digits) + ","
      + "\"point\":" + DoubleToString(SymbolInfoDouble(_Symbol, SYMBOL_POINT), 10) + ","
      + "\"digits\":" + IntegerToString((long)_Digits) + ","
      + "\"bar\":{"
      + "\"time\":" + IntegerToString((long)bar_time) + ","
      + "\"closed\":true,"
      + "\"open\":" + DoubleToString(bars[0].open, _Digits) + ","
      + "\"high\":" + DoubleToString(bars[0].high, _Digits) + ","
      + "\"low\":" + DoubleToString(bars[0].low, _Digits) + ","
      + "\"close\":" + DoubleToString(bars[0].close, _Digits) + ","
      + "\"tick_volume\":" + IntegerToString((long)bars[0].tick_volume)
      + "},"
      + "\"telemetry_only\":true"
      + "}";

   string reply;
   int code;
   bool ok = PostJson("/events", payload, reply, code);
   if(!ok)
      PrintFormat("Ramon Django market snapshot failed: HTTP=%d response=%s", code, reply);
   else if(bar_time != g_last_bar_time)
   {
      g_last_bar_time = bar_time;
      PrintFormat("Ramon Django telemetry accepted: symbol=%s closed_M15_bar=%s",
                  _Symbol, TimeToString(bar_time, TIME_DATE|TIME_MINUTES));
   }
   return ok;
}

int OnInit()
{
   if(StringLen(InstanceId) < 3)
   {
      Print("Ramon Django EA initialization blocked: set a unique InstanceId in Inputs.");
      return INIT_PARAMETERS_INCORRECT;
   }
   if(TimerSeconds < 5 || RequestTimeoutMs < 500)
   {
      Print("Ramon Django EA initialization blocked: timer must be >=5s and timeout >=500ms.");
      return INIT_PARAMETERS_INCORRECT;
   }
   if(COMMAND_POLLING_IMPLEMENTED || ORDER_EXECUTION_IMPLEMENTED)
   {
      Print("Safety invariant failed: this prototype must remain telemetry-only.");
      return INIT_FAILED;
   }

   EventSetTimer(TimerSeconds);
   Print("Ramon Django EA 0.10.0 started in TELEMETRY-ONLY mode. It cannot place or manage orders.");
   Print("Add this host to MT5 Tools > Options > Expert Advisors > Allow WebRequest for listed URL: ",
         DjangoBaseUrl);
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   EventKillTimer();
}

void OnTimer()
{
   if(!EnableTelemetry)
      return;
   SendHeartbeat();
   SendMarketSnapshot();
}

void OnTick()
{
   // Intentionally empty: all telemetry is rate-limited by OnTimer.
   // No order, modify, close, or command-polling functions exist in this prototype.
}
