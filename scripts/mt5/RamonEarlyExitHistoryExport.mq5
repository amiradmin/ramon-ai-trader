// RamonEarlyExitHistoryExport.mq5
// Read-only MT5 script. No trade/order operations. Run from MQL5/Scripts.
// Exports broker M1 bars and Bid/Ask ticks from three fixed research windows.
// Requested timestamps are interpreted as MT5 API/terminal timestamps. Check UTC
// alignment against known M5 close prices before importing anything.
#property copyright "Ramon AI Trader"
#property version   "1.00"
#property script_show_inputs

input string InpSymbol = "XAUUSD_l";
input bool InpExportTicks = true;
input int InpTickChunkSeconds = 60;
input int InpMaxTicksPerChunk = 250000;

struct ExportWindow
{
   string tag;
   string from_text;
   string to_text;
};

bool AllowedSymbol(const string s)
{
   return StringFind(s, "XAUUSD") == 0;
}

bool WriteBars(const string sym, const string tag, const datetime start_at, const datetime end_at)
{
   string filename = "Ramon_EarlyExit_" + sym + "_" + tag + "_M1.csv";
   int fd = FileOpen(filename, FILE_WRITE|FILE_CSV|FILE_ANSI|FILE_COMMON, ',');
   if(fd == INVALID_HANDLE) { Print("RAMON_EXPORT ERROR M1 open ", filename, " err=",GetLastError()); return false; }
   FileWrite(fd,"time","open","high","low","close","spread_points");
   MqlRates rates[];
   ResetLastError();
   int n = CopyRates(sym, PERIOD_M1, start_at, end_at-1, rates);
   if(n < 0)
   {
      Print("RAMON_EXPORT ERROR CopyRates ",tag," err=",GetLastError());
      FileClose(fd);
      return false;
   }
   int kept=0;
   for(int i=0;i<n;i++)
   {
      if(rates[i].time < start_at || rates[i].time >= end_at) continue;
      // CopyRates may return currently-forming candles: exclude those.
      if(rates[i].time + 60 > TimeCurrent()) continue;
      FileWrite(fd, (long)rates[i].time,
                DoubleToString(rates[i].open, _Digits),
                DoubleToString(rates[i].high, _Digits),
                DoubleToString(rates[i].low, _Digits),
                DoubleToString(rates[i].close, _Digits),
                (int)rates[i].spread);
      kept++;
   }
   FileClose(fd);
   Print("RAMON_EXPORT M1 ",tag," rows=",kept," file=Common/Files/",filename,
         " first_window=",TimeToString(start_at,TIME_DATE|TIME_SECONDS),
         " last_window=",TimeToString(end_at,TIME_DATE|TIME_SECONDS));
   return kept>0;
}

bool WriteTicks(const string sym, const string tag, const datetime start_at, const datetime end_at)
{
   string filename = "Ramon_EarlyExit_" + sym + "_" + tag + "_Ticks.csv";
   int fd = FileOpen(filename, FILE_WRITE|FILE_CSV|FILE_ANSI|FILE_COMMON, ',');
   if(fd == INVALID_HANDLE) { Print("RAMON_EXPORT ERROR tick open ",filename," err=",GetLastError()); return false; }
   FileWrite(fd,"time_ms","bid","ask");
   int chunk = MathMax(1,MathMin(300,InpTickChunkSeconds));
   ulong first_ms = (ulong)((long)start_at)*1000;
   ulong end_ms = (ulong)((long)end_at)*1000;
   ulong chunk_ms = (ulong)chunk*1000;
   long written=0, empty_chunks=0, bad_chunks=0, skipped=0;
   MqlTick ticks[];
   for(ulong at=first_ms;at<end_ms;at+=chunk_ms)
   {
      ulong upper=at+chunk_ms-1;
      if(upper>=end_ms) upper=end_ms-1;
      ResetLastError();
      int n=CopyTicksRange(sym,ticks,COPY_TICKS_ALL,at,upper);
      if(n<0) { bad_chunks++; Print("RAMON_EXPORT ERROR CopyTicksRange ",tag," at_ms=",(long)at," err=",GetLastError()); continue; }
      if(n==0) { empty_chunks++; continue; }
      if(n>=InpMaxTicksPerChunk)
      {
         bad_chunks++;
         Print("RAMON_EXPORT WARNING tick chunk reached limit ",tag," at_ms=",(long)at," n=",n);
      }
      for(int i=0;i<n;i++)
      {
         if(ticks[i].time_msc< (long)at || ticks[i].time_msc>(long)upper) continue;
         if(ticks[i].bid<=0 || ticks[i].ask<=0 || ticks[i].ask<ticks[i].bid) { skipped++; continue; }
         FileWrite(fd,ticks[i].time_msc,
                   DoubleToString(ticks[i].bid,_Digits),
                   DoubleToString(ticks[i].ask,_Digits));
         written++;
      }
   }
   FileClose(fd);
   Print("RAMON_EXPORT TICKS ",tag," rows=",written," empty_chunks=",empty_chunks,
         " error_or_limit_chunks=",bad_chunks," bad_quotes=",skipped,
         " file=Common/Files/",filename);
   return written>0 && bad_chunks==0;
}

void OnStart()
{
   if(!AllowedSymbol(InpSymbol)) { Print("RAMON_EXPORT STOP unexpected symbol ",InpSymbol); return; }
   if(!SymbolSelect(InpSymbol,true)) { Print("RAMON_EXPORT STOP unable to select ",InpSymbol); return; }
   Print("RAMON_EXPORT START no orders will be placed. UTC must be verified against M5 before DB import.");
   ExportWindow w[3];
   w[0].tag="20261007"; w[0].from_text="2026.10.07 03:30:00"; w[0].to_text="2026.10.07 10:30:00";
   w[1].tag="20261008"; w[1].from_text="2026.10.08 09:45:00"; w[1].to_text="2026.10.08 19:00:00";
   w[2].tag="20261013"; w[2].from_text="2026.10.13 04:15:00"; w[2].to_text="2026.10.13 06:00:00";
   for(int i=0;i<3;i++)
   {
      datetime a=StringToTime(w[i].from_text), b=StringToTime(w[i].to_text);
      if(a<=0 || b<=a) { Print("RAMON_EXPORT STOP invalid window ",w[i].tag); continue; }
      bool bars_ok=WriteBars(InpSymbol,w[i].tag,a,b);
      bool ticks_ok=true;
      if(InpExportTicks) ticks_ok=WriteTicks(InpSymbol,w[i].tag,a,b);
      Print("RAMON_EXPORT WINDOW ",w[i].tag," bars=",bars_ok," ticks=",ticks_ok);
   }
   Print("RAMON_EXPORT DONE. Files are in MT5 Common/Files. Missing history is not backfilled with synthetic data.");
}
