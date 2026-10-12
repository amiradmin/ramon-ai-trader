#property strict
#property script_show_inputs

// Operator review for one identified broker Stop Out; no trading APIs.
// The EA still enforces every normal risk guard. Do not run with Algo Trading on.
input string OperatorConfirmation="";

void OnStart()
{
   const long expected_login=89166482;
   const string expected_server="LiteFinance-MT5-Live";
   const ulong expected_deal=50332232739;
   const long expected_msc=1791431330002;
   const double expected_test_lock=1791648001.0;
   const string confirmation="REVIEW_STOP_OUT_50332232739";
   if((bool)MQLInfoInteger(MQL_TESTER) ||
      AccountInfoInteger(ACCOUNT_LOGIN)!=expected_login ||
      AccountInfoString(ACCOUNT_SERVER)!=expected_server ||
      !TerminalInfoInteger(TERMINAL_CONNECTED) ||
      TerminalInfoInteger(TERMINAL_TRADE_ALLOWED) ||
      OperatorConfirmation!=confirmation)
   { Print("RAMON_SO_REVIEW REFUSED: identity/connection/algo/confirmation"); return; }
   if(PositionsTotal()!=0 || OrdersTotal()!=0)
   { Print("RAMON_SO_REVIEW REFUSED: positions or pending orders"); return; }
   double equity=AccountInfoDouble(ACCOUNT_EQUITY);
   double margin=AccountInfoDouble(ACCOUNT_MARGIN);
   double free_margin=AccountInfoDouble(ACCOUNT_MARGIN_FREE);
   if(equity<=0.0 || margin>0.000001 || free_margin<=0.0)
   { Print("RAMON_SO_REVIEW REFUSED: account equity/margin"); return; }
   string lock_key="RAMON_V2_STOP_OUT_"+IntegerToString(expected_login);
   double lock_value=0.0;
   if(!GlobalVariableGet(lock_key,lock_value) || lock_value!=expected_test_lock)
   { Print("RAMON_SO_REVIEW REFUSED: exact test lock missing or changed"); return; }
   datetime now=TimeCurrent();
   if(now<=0 || !HistorySelect(0,now))
   { Print("RAMON_SO_REVIEW REFUSED: history unavailable"); return; }
   int found=0;
   for(int i=0;i<HistoryDealsTotal();i++)
   {
      ulong ticket=HistoryDealGetTicket(i);
      long why=0,msc=0;
      if(ticket==0 || !HistoryDealGetInteger(ticket,DEAL_REASON,why))
      { Print("RAMON_SO_REVIEW REFUSED: unreadable deal"); return; }
      if(why!=DEAL_REASON_SO) continue;
      if(!HistoryDealGetInteger(ticket,DEAL_TIME_MSC,msc))
      { Print("RAMON_SO_REVIEW REFUSED: unreadable stop-out timestamp"); return; }
      if(ticket!=expected_deal || msc!=expected_msc)
      { PrintFormat("RAMON_SO_REVIEW REFUSED: additional stop-out deal=%I64u",ticket); return; }
      found++;
   }
   if(found!=1)
   { PrintFormat("RAMON_SO_REVIEW REFUSED: expected one stop-out, got %d",found); return; }

   // Same scope algorithm as Ramon.mq5: SHA256(login|server), first 16 bytes.
   uchar data[],key[],digest[];
   string identity=IntegerToString(expected_login)+"|"+expected_server;
   int size=StringToCharArray(identity,data,0,WHOLE_ARRAY,CP_UTF8);
   if(size<=1) { Print("RAMON_SO_REVIEW REFUSED: invalid identity"); return; }
   ArrayResize(data,size-1);
   if(CryptEncode(CRYPT_HASH_SHA256,data,key,digest)!=32)
   { Print("RAMON_SO_REVIEW REFUSED: unable to compute scope"); return; }
   string scope="";
   for(int i=0;i<16;i++) scope+=StringFormat("%02x",(uint)digest[i]);
   string review_key="R2SO_ACK_"+scope+"_"+StringFormat("%I64u",expected_deal);

   // Recheck critical state just before writing the exact-deal review marker.
   if(!TerminalInfoInteger(TERMINAL_CONNECTED) ||
      TerminalInfoInteger(TERMINAL_TRADE_ALLOWED) ||
      AccountInfoInteger(ACCOUNT_LOGIN)!=expected_login ||
      AccountInfoString(ACCOUNT_SERVER)!=expected_server ||
      PositionsTotal()!=0 || OrdersTotal()!=0 ||
      !GlobalVariableCheck(lock_key) ||
      GlobalVariableGet(lock_key)!=expected_test_lock)
   { Print("RAMON_SO_REVIEW REFUSED: state changed"); return; }
   if(GlobalVariableSet(review_key,(double)expected_msc)==0)
   { Print("RAMON_SO_REVIEW FAILED: review marker write"); return; }
   GlobalVariablesFlush();
   double stored=0.0;
   if(!GlobalVariableGet(review_key,stored) || stored!=(double)expected_msc)
   { Print("RAMON_SO_REVIEW FAILED: review marker verification"); return; }
   PrintFormat("RAMON_SO_REVIEW PASS deal=%I64u; exact event reviewed, lock NOT deleted. Reinitialize updated Ramon with Algo Trading still OFF to run safe release checks.",expected_deal);
}
