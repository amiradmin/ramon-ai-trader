#property strict
#property script_show_inputs

// Read-only broker Stop Out audit. This script never sends orders, closes
// positions, or modifies terminal global variables. It only prints the
// DEAL_REASON_SO records that make Ramon keep its entry lock.
input int LookbackDays=3650;

string DealEntryText(const long value)
{
   if(value==DEAL_ENTRY_IN) return "IN";
   if(value==DEAL_ENTRY_OUT) return "OUT";
   if(value==DEAL_ENTRY_INOUT) return "INOUT";
   if(value==DEAL_ENTRY_OUT_BY) return "OUT_BY";
   return IntegerToString(value);
}

void OnStart()
{
   if(LookbackDays<1 || LookbackDays>36500)
   {
      Print("RAMON_STOP_OUT_AUDIT REFUSED invalid LookbackDays");
      return;
   }

   datetime now=TimeCurrent();
   if(now<=0 || !HistorySelect(now-(datetime)LookbackDays*86400,now))
   {
      Print("RAMON_STOP_OUT_AUDIT FAILED history unavailable");
      return;
   }

   PrintFormat("RAMON_STOP_OUT_AUDIT START login=%I64d server=%s connected=%s range_days=%d deals=%d",
      AccountInfoInteger(ACCOUNT_LOGIN),
      AccountInfoString(ACCOUNT_SERVER),
      TerminalInfoInteger(TERMINAL_CONNECTED) ? "YES" : "NO",
      LookbackDays,
      HistoryDealsTotal());

   int stopout_count=0;
   for(int i=0;i<HistoryDealsTotal();i++)
   {
      ulong ticket=HistoryDealGetTicket(i);
      if(ticket==0) continue;
      long reason=HistoryDealGetInteger(ticket,DEAL_REASON);
      if(reason!=DEAL_REASON_SO) continue;

      stopout_count++;
      long at_msc=HistoryDealGetInteger(ticket,DEAL_TIME_MSC);
      datetime at=(datetime)HistoryDealGetInteger(ticket,DEAL_TIME);
      double net=HistoryDealGetDouble(ticket,DEAL_PROFIT)
         +HistoryDealGetDouble(ticket,DEAL_COMMISSION)
         +HistoryDealGetDouble(ticket,DEAL_SWAP)
         +HistoryDealGetDouble(ticket,DEAL_FEE);
      PrintFormat(
         "RAMON_STOP_OUT_AUDIT RECORD ticket=%I64u time_server=%s time_msc=%I64d symbol=%s entry=%s type=%s volume=%.4f price=%.5f net=%.2f profit=%.2f commission=%.2f swap=%.2f fee=%.2f position=%I64u order=%I64u magic=%I64d comment=%s",
         ticket,
         TimeToString(at,TIME_DATE|TIME_SECONDS),
         at_msc,
         HistoryDealGetString(ticket,DEAL_SYMBOL),
         DealEntryText(HistoryDealGetInteger(ticket,DEAL_ENTRY)),
         EnumToString((ENUM_DEAL_TYPE)HistoryDealGetInteger(ticket,DEAL_TYPE)),
         HistoryDealGetDouble(ticket,DEAL_VOLUME),
         HistoryDealGetDouble(ticket,DEAL_PRICE),
         net,
         HistoryDealGetDouble(ticket,DEAL_PROFIT),
         HistoryDealGetDouble(ticket,DEAL_COMMISSION),
         HistoryDealGetDouble(ticket,DEAL_SWAP),
         HistoryDealGetDouble(ticket,DEAL_FEE),
         (ulong)HistoryDealGetInteger(ticket,DEAL_POSITION_ID),
         (ulong)HistoryDealGetInteger(ticket,DEAL_ORDER),
         HistoryDealGetInteger(ticket,DEAL_MAGIC),
         HistoryDealGetString(ticket,DEAL_COMMENT));
   }

   PrintFormat("RAMON_STOP_OUT_AUDIT COMPLETE stop_out_records=%d; read_only=YES",stopout_count);
}
