#property strict
#property script_show_inputs
#property description "Export closed M15 bars for Ramon using raw MT5 epoch timestamps."

input string ExportSymbol = "XAUUSD_l";
input int BarsToExport = 10000;
input string OutputFileName = "Ramon_XAUUSD_l_M15_History.csv";

void OnStart()
{
   if(BarsToExport<128)
   {
      Print("BarsToExport must be >=128");
      return;
   }
   if(!SymbolSelect(ExportSymbol,true))
   {
      Print("Unable to select symbol ",ExportSymbol);
      return;
   }

   MqlRates rates[];
   ArraySetAsSeries(rates,false);
   int copied=CopyRates(ExportSymbol,PERIOD_M15,1,BarsToExport,rates);
   if(copied<=0)
   {
      Print("CopyRates failed err=",GetLastError());
      return;
   }

   int handle=FileOpen(
      OutputFileName,
      FILE_WRITE|FILE_CSV|FILE_ANSI|FILE_COMMON,
      ','
   );
   if(handle==INVALID_HANDLE)
   {
      Print("FileOpen failed err=",GetLastError());
      return;
   }

   FileWrite(handle,"time","open","high","low","close","spread_points");
   for(int i=0;i<copied;i++)
   {
      FileWrite(
         handle,
         (long)rates[i].time,
         DoubleToString(rates[i].open,_Digits),
         DoubleToString(rates[i].high,_Digits),
         DoubleToString(rates[i].low,_Digits),
         DoubleToString(rates[i].close,_Digits),
         (int)rates[i].spread
      );
   }
   FileClose(handle);
   Print("Ramon history export complete: ",copied,
         " bars -> FILE_COMMON/",OutputFileName);
}
