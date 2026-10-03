#property strict
#property script_show_inputs
#property description "Export XAUUSD_l broker bid/ask ticks for isolated Eliot research. No trades."

input string ExportSymbol = "XAUUSD_l";
input int DaysBack = 2;
input int MaxTicksToExport = 300000;
input string OutputFileName = "Eliot_XAUUSD_l_Ticks.csv";

void OnStart()
{
   if(DaysBack<1 || DaysBack>30 || MaxTicksToExport<1000 || OutputFileName=="")
   {
      Print("Invalid Eliot tick export inputs");
      return;
   }
   if(!SymbolSelect(ExportSymbol,true))
   {
      Print("Unable to select Eliot symbol ",ExportSymbol);
      return;
   }
   // Use MT5's broker timeline, matching the M5 bars exported by CopyRates.
   ulong finish=(ulong)TimeCurrent()*1000;
   ulong begin=finish-(ulong)DaysBack*86400000;
   int handle=FileOpen(OutputFileName,FILE_WRITE|FILE_CSV|FILE_ANSI|FILE_COMMON,',');
   if(handle==INVALID_HANDLE)
   {
      Print("Eliot FileOpen failed error=",GetLastError());
      return;
   }
   FileWrite(handle,"time_msc","bid","ask","flags");
   int digits=(int)SymbolInfoInteger(ExportSymbol,SYMBOL_DIGITS);
   int written=0;
   bool failed=false;
   bool capped=false;
   for(ulong from=begin;from<finish;)
   {
      ulong to=from+900000-1; // 15-minute chunks; no shared boundary tick.
      if(to>=finish)
         to=finish-1;
      MqlTick ticks[];
      ResetLastError();
      int copied=CopyTicksRange(ExportSymbol,ticks,COPY_TICKS_INFO,from,to);
      if(copied<0)
      {
         Print("Eliot CopyTicksRange failed at ",from," error=",GetLastError());
         failed=true;
         break;
      }
      for(int i=0;i<copied;i++)
      {
         if(ticks[i].bid<=0 || ticks[i].ask<=ticks[i].bid)
            continue;
         FileWrite(handle,(long)ticks[i].time_msc,
                   DoubleToString(ticks[i].bid,digits),
                   DoubleToString(ticks[i].ask,digits),(int)ticks[i].flags);
         written++;
         if(written>=MaxTicksToExport)
         {
            capped=true;
            break;
         }
      }
      if(capped)
         break;
      from=to+1;
   }
   FileClose(handle);
   if(failed)
   {
      FileDelete(OutputFileName,FILE_COMMON);
      Print("Eliot tick export FAILED; incomplete CSV removed");
      return;
   }
   Print("Eliot tick export complete: ",written," rows -> FILE_COMMON/",OutputFileName,
         " capped=",capped ? "YES (partial date range)" : "NO");
}
