#property strict
#property version "1.00"
#property description "Read-only M1/M5 closed bar exporter for Ramon. Attach to one chart; no trade APIs."

input string ExportSymbol=""; // Empty: use chart symbol (demo and cent variants).
string ActiveExportSymbol="";
input int ExportEverySeconds=60;
input int ExportClosedBars=1500;

void ExportBars(ENUM_TIMEFRAMES timeframe,string label)
{
   MqlRates bars[];
   ArraySetAsSeries(bars,false);
   ResetLastError();
   int copied=CopyRates(ActiveExportSymbol,timeframe,1,ExportClosedBars,bars);
   if(copied<=0)
   {
      PrintFormat("RAMON_MTF_EXPORT FAIL tf=%s CopyRates err=%d",label,GetLastError());
      return;
   }
   string filename="RamonLive_"+ActiveExportSymbol+"_"+label+".csv";
   string tmp=filename+".tmp";
   int h=FileOpen(tmp,FILE_WRITE|FILE_CSV|FILE_ANSI|FILE_COMMON,',',CP_UTF8);
   if(h==INVALID_HANDLE)
   { PrintFormat("RAMON_MTF_EXPORT FAIL tf=%s open err=%d",label,GetLastError()); return; }
   FileWrite(h,"time","open","high","low","close","spread_points");
   int digits=(int)SymbolInfoInteger(ActiveExportSymbol,SYMBOL_DIGITS);
   long newest=0;
   for(int i=0;i<copied;i++)
   {
      // CopyRates(start_pos=1) excludes the incomplete candle.
      FileWrite(h,(long)bars[i].time,
         DoubleToString(bars[i].open,digits),
         DoubleToString(bars[i].high,digits),
         DoubleToString(bars[i].low,digits),
         DoubleToString(bars[i].close,digits),(int)bars[i].spread);
      if((long)bars[i].time>newest)newest=(long)bars[i].time;
   }
   FileFlush(h);
   FileClose(h);
   // Publish a full snapshot, never a partially written CSV.
   ResetLastError();
   if(!FileMove(tmp,FILE_COMMON,filename,FILE_COMMON|FILE_REWRITE))
   { PrintFormat("RAMON_MTF_EXPORT FAIL tf=%s publish err=%d",label,GetLastError()); return; }
   PrintFormat("RAMON_MTF_EXPORT OK tf=%s rows=%d latest=%I64d",label,copied,newest);
}

void OnTimer()
{
   if(!TerminalInfoInteger(TERMINAL_CONNECTED))return;
   ExportBars(PERIOD_M1,"M1");
   ExportBars(PERIOD_M5,"M5");
}
int OnInit()
{
   ActiveExportSymbol=(ExportSymbol=="" ? _Symbol : ExportSymbol);
   if(StringFind(ActiveExportSymbol,"XAUUSD")!=0 || ExportEverySeconds<15 || ExportClosedBars<50 ||
      ExportClosedBars>10000 || !SymbolSelect(ActiveExportSymbol,true))
      return INIT_PARAMETERS_INCORRECT;
   if(!EventSetTimer(ExportEverySeconds))return INIT_FAILED;
   Print("RAMON_MTF_EXPORT read-only collector started; no order execution");
   OnTimer();
   return INIT_SUCCEEDED;
}
void OnDeinit(const int reason){EventKillTimer();}
