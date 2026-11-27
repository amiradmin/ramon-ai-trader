//+------------------------------------------------------------------+
//| Ramon Contract Snapshot — read-only, no orders / account changes |
//| Compile as an MQL5 script and run once in connected MT5 terminal. |
//+------------------------------------------------------------------+
#property strict
#property script_show_inputs
input string ResearchSymbol = "XAUUSD_l";

string Num(double value, int digits=8)
{
   return DoubleToString(value,digits);
}

void OnStart()
{
   string symbol=ResearchSymbol;
   if(symbol=="")
   {
      Print("RAMON_LAB_ERROR: Empty symbol");
      return;
   }
   if(!SymbolSelect(symbol,true))
   {
      Print("RAMON_LAB_ERROR: Symbol not available: ",symbol);
      return;
   }
   long digits=0;
   if(!SymbolInfoInteger(symbol,SYMBOL_DIGITS,digits))
   {
      Print("RAMON_LAB_ERROR: Could not get symbol details");
      return;
   }

   string filename="ramon-contract-snapshot.json";
   int file=FileOpen(filename,FILE_WRITE|FILE_TXT|FILE_ANSI);
   if(file==INVALID_HANDLE)
   {
      Print("RAMON_LAB_ERROR: FileOpen failed: ",GetLastError());
      return;
   }
   // The values below contain no account login, server, passwords or tokens.
   // Symbol is a user-supplied MT5 symbol: restrict quotes/backslashes to
   // prevent writing invalid JSON.
   if(StringFind(symbol,"\"")>=0 || StringFind(symbol,"\\")>=0)
   {
      FileClose(file);
      FileDelete(filename);
      Print("RAMON_LAB_ERROR: Symbol cannot contain quotes or backslashes");
      return;
   }
   string currency=AccountInfoString(ACCOUNT_CURRENCY);
   if(StringFind(currency,"\"")>=0 || StringFind(currency,"\\")>=0)
   {
      FileClose(file);
      FileDelete(filename);
      Print("RAMON_LAB_ERROR: Unusual account currency string");
      return;
   }
   string json="{\n";
   json+="  \"source\": \"MT5_CONNECTED_TERMINAL_READ_ONLY\",\n";
   json+="  \"symbol\": \""+symbol+"\",\n";
   json+="  \"currency\": \""+currency+"\",\n";
   json+="  \"balance\": "+Num(AccountInfoDouble(ACCOUNT_BALANCE),8)+",\n";
   json+="  \"broker_fields\": {\n";
   json+="    \"point\": "+Num(SymbolInfoDouble(symbol,SYMBOL_POINT))+",\n";
   json+="    \"trade_tick_size\": "+Num(SymbolInfoDouble(symbol,SYMBOL_TRADE_TICK_SIZE))+",\n";
   json+="    \"trade_tick_value\": "+Num(SymbolInfoDouble(symbol,SYMBOL_TRADE_TICK_VALUE))+",\n";
   json+="    \"trade_tick_value_profit\": "+Num(SymbolInfoDouble(symbol,SYMBOL_TRADE_TICK_VALUE_PROFIT))+",\n";
   json+="    \"trade_tick_value_loss\": "+Num(SymbolInfoDouble(symbol,SYMBOL_TRADE_TICK_VALUE_LOSS))+",\n";
   json+="    \"trade_contract_size\": "+Num(SymbolInfoDouble(symbol,SYMBOL_TRADE_CONTRACT_SIZE))+",\n";
   json+="    \"volume_min\": "+Num(SymbolInfoDouble(symbol,SYMBOL_VOLUME_MIN))+",\n";
   json+="    \"volume_max\": "+Num(SymbolInfoDouble(symbol,SYMBOL_VOLUME_MAX))+",\n";
   json+="    \"volume_step\": "+Num(SymbolInfoDouble(symbol,SYMBOL_VOLUME_STEP))+",\n";
   json+="    \"trade_stops_level\": "+IntegerToString(SymbolInfoInteger(symbol,SYMBOL_TRADE_STOPS_LEVEL))+",\n";
   json+="    \"trade_freeze_level\": "+IntegerToString(SymbolInfoInteger(symbol,SYMBOL_TRADE_FREEZE_LEVEL))+"\n";
   json+="  },\n";
   json+="  \"note\": \"Read-only current MT5 symbol and account metadata; no orders executed\"\n";
   json+="}\n";
   FileWriteString(file,json);
   FileClose(file);
   Print("RAMON_LAB_EXPORT_OK: ",TerminalInfoString(TERMINAL_DATA_PATH),"\\MQL5\\Files\\",filename);
   Print("RAMON_LAB_NOTE: Copy locally into laboratory, never commit financial snapshot");
}
