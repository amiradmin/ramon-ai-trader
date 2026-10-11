// Ramon historical Bid/Ask tick exporter — research only, NEVER places orders.
// Install under MQL5/Scripts and run manually from MT5 Navigator.
// The times below are MT5 terminal/broker datetime values. Verify clock
// conversion to UTC before combining with decision timestamps.
#property strict
#property script_show_inputs

input string InpSymbol = "XAUUSD_l";
input datetime InpFrom = D'2026.10.01 00:00:00';
input datetime InpTo = D'2026.10.02 00:00:00';
input int InpChunkSeconds = 600;
input int InpMaxTicksPerChunk = 200000;
input string InpOutput = "Ramon_BidAsk_Ticks.csv";

void OnStart()
{
   if(InpSymbol == "" || InpFrom >= InpTo || InpChunkSeconds < 1 ||
      InpChunkSeconds > 3600 || InpMaxTicksPerChunk < 1)
   {
      Print("RAMON TICK EXPORT: invalid input parameters");
      return;
   }
   if(!SymbolSelect(InpSymbol, true))
   {
      Print("RAMON TICK EXPORT: symbol unavailable ", InpSymbol);
      return;
   }
   int handle = FileOpen(InpOutput, FILE_WRITE|FILE_CSV|FILE_ANSI|FILE_COMMON, ',');
   if(handle == INVALID_HANDLE)
   {
      Print("RAMON TICK EXPORT: FileOpen failed ", GetLastError());
      return;
   }
   FileWrite(handle, "time_ms", "bid", "ask");
   long start_ms = (long)InpFrom * 1000;
   long limit_ms = (long)InpTo * 1000;
   long chunk_ms = (long)InpChunkSeconds * 1000;
   long count = 0;
   long filtered = 0;
   int failures = 0;
   bool complete = true;
   for(long left = start_ms; left < limit_ms; )
   {
      // CopyTicksRange uses inclusive bounds: stop at exclusive end - 1.
      long right_exclusive = left + chunk_ms;
      if(right_exclusive > limit_ms)
         right_exclusive = limit_ms;
      MqlTick ticks[];
      ResetLastError();
      int found = CopyTicksRange(InpSymbol, ticks, COPY_TICKS_INFO,
                                 (ulong)left, (ulong)(right_exclusive - 1));
      if(found < 0)
      {
         Print("RAMON TICK EXPORT: CopyTicksRange error ", GetLastError(),
               " at ", left);
         failures++;
         complete = false;
         break; // Never silently skip missing chunks.
      }
      if(found >= InpMaxTicksPerChunk)
      {
         Print("RAMON TICK EXPORT: tick cap reached (", found,
               ") at ", left, "; reduce InpChunkSeconds and retry");
         complete = false;
         break; // Avoid declaring a potentially truncated archive complete.
      }
      for(int i = 0; i < found; i++)
      {
         if(ticks[i].bid <= 0 || ticks[i].ask <= ticks[i].bid)
         {
            filtered++;
            continue;
         }
         FileWrite(handle, StringFormat("%I64d", ticks[i].time_msc),
                   DoubleToString(ticks[i].bid, (int)SymbolInfoInteger(InpSymbol, SYMBOL_DIGITS)),
                   DoubleToString(ticks[i].ask, (int)SymbolInfoInteger(InpSymbol, SYMBOL_DIGITS)));
         count++;
      }
      left = right_exclusive;
   }
   FileFlush(handle);
   FileClose(handle);
   Print("RAMON TICK EXPORT: rows=", count, " filtered=", filtered,
         " errors=", failures, " complete=", complete,
         " FILE_COMMON=", InpOutput);
   if(!complete || filtered > 0)
      Print("RAMON TICK EXPORT: inspect logs and coverage; export is NOT validated.");
}
