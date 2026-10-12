#property strict
#property script_show_inputs

// One-time creation of an EMPTY, account/server-scoped execution journal.
// Never trades, removes locks, overwrites an existing journal, or repairs
// incomplete/corrupt journals. Only use while automated trading is DISABLED.
input string OperatorConfirmation="";

string ProvisionHash(string value)
{
   uchar data[],key[],digest[];
   int size=StringToCharArray(value,data,0,WHOLE_ARRAY,CP_UTF8);
   if(size<=1) return "";
   ArrayResize(data,size-1);
   if(CryptEncode(CRYPT_HASH_SHA256,data,key,digest)!=32) return "";
   string result="";
   for(int i=0;i<32;i++) result+=StringFormat("%02x",(uint)digest[i]);
   return result;
}

void OnStart()
{
   const string confirmation="PROVISION_RAMON_EXECUTION_JOURNAL";
   Print("RAMON_EXEC_PROVISION START; no trading; existing journals never overwritten");
   if((bool)MQLInfoInteger(MQL_TESTER) ||
      OperatorConfirmation!=confirmation ||
      !TerminalInfoInteger(TERMINAL_CONNECTED) ||
      TerminalInfoInteger(TERMINAL_TRADE_ALLOWED))
   { Print("RAMON_EXEC_PROVISION REFUSED confirmation/connection/algo"); return; }

   long login=AccountInfoInteger(ACCOUNT_LOGIN);
   string server=AccountInfoString(ACCOUNT_SERVER);
   if(login<=0 || server=="")
   { Print("RAMON_EXEC_PROVISION REFUSED account identity"); return; }
   if(PositionsTotal()!=0 || OrdersTotal()!=0)
   { PrintFormat("RAMON_EXEC_PROVISION REFUSED positions=%d orders=%d",PositionsTotal(),OrdersTotal()); return; }
   if(AccountInfoDouble(ACCOUNT_EQUITY)<=0.0 ||
      AccountInfoDouble(ACCOUNT_MARGIN)>0.000001 ||
      AccountInfoDouble(ACCOUNT_MARGIN_FREE)<=0.0)
   { Print("RAMON_EXEC_PROVISION REFUSED account margin/equity"); return; }

   datetime now=TimeCurrent();
   if(now<=0 || !HistorySelect(0,now))
   { Print("RAMON_EXEC_PROVISION REFUSED broker history unavailable"); return; }
   string scope=ProvisionHash(IntegerToString(login)+"|"+server);
   if(scope=="") { Print("RAMON_EXEC_PROVISION REFUSED scope hash"); return; }
   scope=StringSubstr(scope,0,32);
   // Unreviewed broker stop-outs must be resolved before preparing live execution.
   for(int i=0;i<HistoryDealsTotal();i++)
   {
      ulong ticket=HistoryDealGetTicket(i);
      long why=0,msc=0;
      if(ticket==0 || !HistoryDealGetInteger(ticket,DEAL_REASON,why))
      { Print("RAMON_EXEC_PROVISION REFUSED unreadable broker deal"); return; }
      if(why!=DEAL_REASON_SO) continue;
      if(!HistoryDealGetInteger(ticket,DEAL_TIME_MSC,msc) || msc<=0 ||
         msc>9007199254740991)
      { Print("RAMON_EXEC_PROVISION REFUSED unreadable stop-out"); return; }
      string ack="R2SO_ACK_"+scope+"_"+StringFormat("%I64u",ticket);
      double reviewed=0.0;
      if(!GlobalVariableGet(ack,reviewed) || reviewed!=(double)msc)
      { PrintFormat("RAMON_EXEC_PROVISION REFUSED unreviewed stop-out=%I64u",ticket); return; }
   }
   if(GlobalVariableCheck("RAMON_V2_STOP_OUT_"+IntegerToString(login)))
   { Print("RAMON_EXEC_PROVISION REFUSED stop-out lock active"); return; }

   string path="RamonV2Execution_"+scope+".journal";
   if(FileIsExist(path,FILE_COMMON))
   { Print("RAMON_EXEC_PROVISION REFUSED journal already exists; never overwrite"); return; }
   if(!TerminalInfoInteger(TERMINAL_CONNECTED) ||
      TerminalInfoInteger(TERMINAL_TRADE_ALLOWED) ||
      login!=AccountInfoInteger(ACCOUNT_LOGIN) ||
      server!=AccountInfoString(ACCOUNT_SERVER) ||
      PositionsTotal()!=0 || OrdersTotal()!=0)
   { Print("RAMON_EXEC_PROVISION REFUSED state changed"); return; }
   // Lock out concurrent initialization: no sharing; never truncate existing file.
   // FILE_READ|FILE_WRITE opens a missing file for creation in MQL5.
   ResetLastError();
   int handle=FileOpen(path,FILE_READ|FILE_WRITE|FILE_TXT|FILE_ANSI|FILE_COMMON,0,CP_UTF8);
   if(handle==INVALID_HANDLE)
   { PrintFormat("RAMON_EXEC_PROVISION FAILED journal open error=%d",GetLastError()); return; }
   // Fail closed if another writer created content between existence check and open.
   if(FileSize(handle)!=0)
   {
      FileClose(handle);
      Print("RAMON_EXEC_PROVISION REFUSED existing journal content");
      return;
   }
   string header="RAMON_EXEC_V1|"+scope;
   string line=header+"|"+ProvisionHash(header);
   ResetLastError();
   uint written=FileWriteString(handle,line+"\r\n");
   FileFlush(handle);
   bool good=written==(uint)(StringLen(line)+2) && GetLastError()==0 &&
      FileSeek(handle,0,SEEK_SET) && FileReadString(handle)==line &&
      GetLastError()==0;
   FileClose(handle);
   if(!good)
   {
      Print("RAMON_EXEC_PROVISION FAILED header verification; journal must be inspected manually; no auto-retry");
      return;
   }
   PrintFormat("RAMON_EXEC_PROVISION PASS account=%I64d server=%s file=%s; keep Algo Trading OFF until normal risk checks pass",login,server,path);
}
