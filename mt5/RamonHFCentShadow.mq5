#property strict
#property version "1.00"
#property description "Isolated HF research collector. No order execution."

input long ExpectedLogin=89166482;
input string ExpectedServer="LiteFinance-MT5-Live";
input string ResearchSymbol="XAUUSD_l";
input int ExportClosedBars=1000;
// Monday 2026-10-12 00:00 Tehran. Fresh broker quotes are also mandatory.
input datetime StartAfterUTC=D'2026.10.11 20:30:00';
datetime LastExport=0;
string LastState="";

void ReportState(const string state)
{
   if(state==LastState) return;
   LastState=state;
   Print("HF_CENT_SHADOW ",state," no_order=true");
}

bool IdentityOK()
{
   return AccountInfoInteger(ACCOUNT_LOGIN)==ExpectedLogin &&
          AccountInfoString(ACCOUNT_SERVER)==ExpectedServer &&
          TerminalInfoInteger(TERMINAL_CONNECTED);
}

int OnInit()
{
   if(ExpectedLogin<=0 || ExpectedServer=="" || ExportClosedBars<300 || ExportClosedBars>10000)
      return INIT_PARAMETERS_INCORRECT;
   if(!SymbolSelect(ResearchSymbol,true)) return INIT_FAILED;
   EventSetTimer(15);
   Print("HF_CENT_SHADOW collector only; execution unavailable; production locks unchanged");
   return INIT_SUCCEEDED;
}

void OnTimer()
{
   if(!IdentityOK()) { ReportState("WAIT account/connection"); return; }
   if(TimeGMT()<StartAfterUTC) { ReportState("WAIT Monday Tehran"); return; }
   MqlTick quote;
   long quote_age=0;
   if(!SymbolInfoTick(ResearchSymbol,quote) || quote.bid<=0 || quote.ask<quote.bid)
   { ReportState("WAIT valid quote"); return; }
   quote_age=(long)TimeTradeServer()-(long)quote.time;
   if(quote_age<0 || quote_age>120)
   { ReportState("WAIT fresh broker quote"); return; }
   ReportState("ACTIVE data collection; model inference blocked");
   datetime closed=iTime(ResearchSymbol,PERIOD_M15,1);
   if(closed<=0 || closed==LastExport) return;
   MqlRates bars[];
   ArraySetAsSeries(bars,false);
   int count=CopyRates(ResearchSymbol,PERIOD_M15,1,ExportClosedBars,bars);
   if(count<300) { Print("HF_CENT_SHADOW WAIT history insufficient"); return; }
   // Separate terminal-local research files, never the production journal.
   FolderCreate("RamonHFCentShadow");
   int handle=FileOpen("RamonHFCentShadow\\closed_m15.csv",FILE_WRITE|FILE_CSV|FILE_ANSI,',',CP_UTF8);
   if(handle==INVALID_HANDLE) { Print("HF_CENT_SHADOW BLOCKED export unavailable"); return; }
   FileWrite(handle,"time_broker","open","high","low","close","tick_volume","spread_points","real_volume");
   for(int i=0;i<count;i++)
      FileWrite(handle,(long)bars[i].time,DoubleToString(bars[i].open,8),
                DoubleToString(bars[i].high,8),DoubleToString(bars[i].low,8),
                DoubleToString(bars[i].close,8),bars[i].tick_volume,bars[i].spread,bars[i].real_volume);
   FileFlush(handle);
   FileClose(handle);
   LastExport=closed;
   // Export times retain broker semantics. Never guess historical UTC offsets.
   PrintFormat("HF_CENT_SHADOW HOLD bars=%d closed_broker=%I64d quote_broker=%I64d server_now=%I64d reason=unverified_feature_pipeline no_order=true",
               count,(long)closed,(long)quote.time,(long)TimeTradeServer());
}

void OnDeinit(const int reason) { EventKillTimer(); }
