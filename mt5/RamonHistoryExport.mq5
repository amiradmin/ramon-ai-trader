#property strict
#property script_show_inputs
#property description "Read-only historical OHLCV exporter. Does not issue trading commands."
input string ExportSymbol="XAUUSD_o";
input int FirstYear=2021;
input int LastYear=2026;
input string ExportTag="ramon-research-20261120";

// Writes unconverted server timestamps. Per-year files make failures visible.
void OnStart()
{
   if(FirstYear<2000 || LastYear<FirstYear || LastYear>2100 || StringLen(ExportTag)==0)
   { Print("Invalid export settings"); return; }
   if(!SymbolSelect(ExportSymbol,true)) { Print("Symbol unavailable: ",ExportSymbol); return; }
   ENUM_TIMEFRAMES tfs[]={PERIOD_M1,PERIOD_M5,PERIOD_M15,PERIOD_H1};
   string names[]={"M1","M5","M15","H1"};
   for(int tf=0;tf<ArraySize(tfs);tf++)
   {
      for(int year=FirstYear;year<=LastYear;year++)
      {
         string name=StringFormat("%s_%s_%s_%d.csv",ExportTag,ExportSymbol,names[tf],year);
         // Fail closed rather than overwrite a previous export.
         if(FileIsExist(name,FILE_COMMON)) { Print("Already exists: ",name); continue; }
         datetime start=StringToTime(StringFormat("%d.01.01 00:00",year));
         datetime end=StringToTime(StringFormat("%d.01.01 00:00",year+1));
         MqlRates bars[];
         int copied=-1;
         for(int attempt=0;attempt<20 && !IsStopped();attempt++)
         {
            ResetLastError();
            copied=CopyRates(ExportSymbol,tfs[tf],start,end-1,bars);
            if(copied>0 && SeriesInfoInteger(ExportSymbol,tfs[tf],SERIES_SYNCHRONIZED)) break;
            Sleep(1000);
         }
         if(copied<=0 || !SeriesInfoInteger(ExportSymbol,tfs[tf],SERIES_SYNCHRONIZED))
         { Print("History unavailable or unsynchronized for ",name," error=",GetLastError()); continue; }
         int handle=FileOpen(name,FILE_WRITE|FILE_CSV|FILE_ANSI|FILE_COMMON,',');
         if(handle==INVALID_HANDLE) { Print("Cannot create ",name," error=",GetLastError()); continue; }
         FileWrite(handle,"broker_timestamp","open","high","low","close","tick_volume","spread_points","real_volume");
         datetime now=TimeTradeServer();
         int written=0;
         for(int i=0;i<copied && !IsStopped();i++)
         {
            // Exclude the forming bar; no timezone conversion is performed.
            if(bars[i].time+PeriodSeconds(tfs[tf])>now) continue;
            FileWrite(handle,TimeToString(bars[i].time,TIME_DATE|TIME_SECONDS),
                      DoubleToString(bars[i].open,8),DoubleToString(bars[i].high,8),
                      DoubleToString(bars[i].low,8),DoubleToString(bars[i].close,8),
                      bars[i].tick_volume,bars[i].spread,bars[i].real_volume);
            written++;
         }
         FileClose(handle);
         if(IsStopped())
         {
            FileDelete(name,FILE_COMMON);
            Print("Interrupted export discarded: ",name);
            return;
         }
         Print("EXPORT ",name," server=",AccountInfoString(ACCOUNT_SERVER),
               " copied=",copied," written=",written," synchronized=",
               SeriesInfoInteger(ExportSymbol,tfs[tf],SERIES_SYNCHRONIZED));
      }
   }
}
