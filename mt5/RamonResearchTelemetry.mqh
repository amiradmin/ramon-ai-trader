// Observational only: no requests, order operations, or account mutations.
// Append-only JSONL compatible with logging/research_ingest.py.
// All timestamps retain raw broker tick milliseconds and observed UTC separately.
#ifndef RAMON_RESEARCH_TELEMETRY_MQH
#define RAMON_RESEARCH_TELEMETRY_MQH
bool RT_Enabled=false;
int RT_IntervalMs=1000;
string RT_RunId="";
long RT_Sequence=0;
long RT_LastTickMsc=0;
ulong RT_LastPosition=0;
double RT_LastSL=0,RT_LastTP=0,RT_LastVolume=0;
bool RT_HadPosition=false;

string RT_JsonEscape(string s)
{
   StringReplace(s,"\\","\\\\");
   StringReplace(s,"\"","\\\"");
   StringReplace(s,"\r","\\r");
   StringReplace(s,"\n","\\n");
   StringReplace(s,"\t","\\t");
   return s;
}
string RT_AccountKey()
{
   return AccountInfoString(ACCOUNT_SERVER)+":"+IntegerToString(AccountInfoInteger(ACCOUNT_LOGIN));
}
string RT_Path(const ulong magic)
{
   string server=AccountInfoString(ACCOUNT_SERVER);
   StringReplace(server,"/","_"); StringReplace(server,"\\","_"); StringReplace(server,":","_");
   return "RamonResearch_"+server+"_"+IntegerToString(AccountInfoInteger(ACCOUNT_LOGIN))
      +"_"+IntegerToString((long)magic)+".jsonl";
}
void RT_Append(const ulong magic,const bool small,const string kind,
               const string payload,const string sample="",const ulong position=0,
               const long tick_msc=0)
{
   if(!RT_Enabled || RT_RunId=="") return;
   int h=FileOpen(RT_Path(magic),FILE_READ|FILE_WRITE|FILE_TXT|FILE_ANSI|FILE_COMMON,0,CP_UTF8);
   if(h==INVALID_HANDLE) { Print("Ramon research logger open failed ",GetLastError()); return; }
   FileSeek(h,0,SEEK_END);
   RT_Sequence++;
   long at=(tick_msc>0 ? tick_msc : (long)TimeCurrent()*1000);
   string id=RT_AccountKey()+":"+IntegerToString((long)magic)+":"+RT_RunId+":"+IntegerToString(RT_Sequence);
   string row="{\"event_id\":\""+RT_JsonEscape(id)+"\",\"account_key\":\""+RT_JsonEscape(RT_AccountKey())
      +"\",\"role\":\""+(small?"SMALL":"MAIN")+"\",\"symbol\":\""+RT_JsonEscape(_Symbol)
      +"\",\"kind\":\""+RT_JsonEscape(kind)+"\",\"sample_key\":\""+RT_JsonEscape(sample)
      +"\",\"position_id\":\""+(position>0 ? IntegerToString((long)position):"")
      +"\",\"broker_time_msc\":"+IntegerToString(at)
      +",\"observed_utc\":"+IntegerToString((long)TimeGMT())
      +",\"data\":{"+payload+"}}\r\n";
   uint written=FileWriteString(h,row);
   FileClose(h);
   if(written==0) Print("Ramon research logger write failed ",GetLastError());
}
void RT_Start(const bool enabled,const int interval_ms,const ulong magic,const bool small)
{
   RT_Enabled=enabled;
   RT_IntervalMs=MathMax(1000,interval_ms);
   RT_RunId=IntegerToString((long)TimeGMT())+"-"+IntegerToString((long)GetMicrosecondCount())+"-"+IntegerToString(ChartID());
   RT_Sequence=0; RT_LastTickMsc=0; RT_HadPosition=false;
   RT_Append(magic,small,"session_start",
      "\"ea_version\":\"0.59.8\",\"source\":\"restored_observational_logger\",\"sampling_interval_ms\":"+IntegerToString(RT_IntervalMs));
}
void RT_Stop(const ulong magic,const bool small,const int reason)
{
   RT_Append(magic,small,"session_end","\"reason\":"+IntegerToString(reason));
   RT_Enabled=false;
}
void RT_ObservePosition(const ulong magic,const bool small,const string sample)
{
   if(!RT_Enabled) return;
   ulong ticket=0;
   for(int i=0;i<PositionsTotal();i++)
   {
      ulong t=PositionGetTicket(i);
      if(t>0 && PositionSelectByTicket(t) && PositionGetString(POSITION_SYMBOL)==_Symbol
         && (ulong)PositionGetInteger(POSITION_MAGIC)==magic) { ticket=t; break; }
   }
   if(ticket==0)
   {
      RT_LastTickMsc=0; RT_LastPosition=0; RT_HadPosition=false;
      return;
   }
   if(!PositionSelectByTicket(ticket)) return;
   MqlTick tick;
   if(!SymbolInfoTick(_Symbol,tick) || tick.time_msc<=0 || tick.bid<=0 || tick.ask<tick.bid) return;
   ulong pos=(ulong)PositionGetInteger(POSITION_IDENTIFIER);
   string comment=PositionGetString(POSITION_COMMENT);
   string key=sample;
   int k=StringFind(comment,"Ramon:");
   if(k>=0 && StringLen(comment)>=k+22) key=StringSubstr(comment,k+6,16);
   double sl=PositionGetDouble(POSITION_SL),tp=PositionGetDouble(POSITION_TP);
   double volume=PositionGetDouble(POSITION_VOLUME);
   if(!RT_HadPosition || pos!=RT_LastPosition || sl!=RT_LastSL || tp!=RT_LastTP || volume!=RT_LastVolume)
   {
      string data="\"source\":\"position_observation\",\"actor\":\"external_unattributed\","
         +"\"previous_known\":"+(RT_HadPosition && pos==RT_LastPosition ? "true":"false")
         +",\"sl_before\":"+DoubleToString(RT_LastSL,8)+",\"sl_after\":"+DoubleToString(sl,8)
         +",\"tp_before\":"+DoubleToString(RT_LastTP,8)+",\"tp_after\":"+DoubleToString(tp,8)
         +",\"volume\":"+DoubleToString(volume,8);
      RT_Append(magic,small,(!RT_HadPosition || pos!=RT_LastPosition)?"protection_baseline":"protection_change",
                data,key,pos,tick.time_msc);
      RT_LastPosition=pos;RT_LastSL=sl;RT_LastTP=tp;RT_LastVolume=volume;RT_HadPosition=true;
      RT_LastTickMsc=0;
   }
   if(tick.time_msc<=RT_LastTickMsc || (RT_LastTickMsc>0 && tick.time_msc-RT_LastTickMsc<RT_IntervalMs)) return;
   if(RT_LastTickMsc>0 && tick.time_msc-RT_LastTickMsc>3*RT_IntervalMs)
      RT_Append(magic,small,"telemetry_gap",
                "\"source\":\"position_quote\",\"gap_ms\":"+IntegerToString(tick.time_msc-RT_LastTickMsc),
                key,pos,tick.time_msc);
   RT_LastTickMsc=tick.time_msc;
   string quote="\"bid\":"+DoubleToString(tick.bid,8)+",\"ask\":"+DoubleToString(tick.ask,8)
      +",\"entry_price\":"+DoubleToString(PositionGetDouble(POSITION_PRICE_OPEN),8)
      +",\"position_type\":"+IntegerToString(PositionGetInteger(POSITION_TYPE))
      +",\"volume\":"+DoubleToString(volume,8)
      +",\"profit_units\":"+DoubleToString(PositionGetDouble(POSITION_PROFIT),8)
      +",\"swap_units\":"+DoubleToString(PositionGetDouble(POSITION_SWAP),8)
      +",\"sl\":"+DoubleToString(sl,8)+",\"tp\":"+DoubleToString(tp,8)
      +",\"tp_stage\":0,\"sampling_interval_ms\":"+IntegerToString(RT_IntervalMs);
   RT_Append(magic,small,"position_quote",quote,key,pos,tick.time_msc);
}
void RT_Deal(const ulong magic,const bool small,const ulong deal)
{
   if(!RT_Enabled || deal==0 || !HistoryDealSelect(deal)) return;
   if(HistoryDealGetString(deal,DEAL_SYMBOL)!=_Symbol) return;
   ulong pos=(ulong)HistoryDealGetInteger(deal,DEAL_POSITION_ID);
   // Closing a position manually often has magic=0. Only include it if position
   // ownership can be established from the opening deal.
   bool owned=((ulong)HistoryDealGetInteger(deal,DEAL_MAGIC)==magic);
   string sample="";
   if(pos>0 && HistorySelectByPosition(pos))
   {
      owned=false;
      for(int i=0;i<HistoryDealsTotal();i++)
      {
         ulong d=HistoryDealGetTicket(i);
         if(d==0 || HistoryDealGetInteger(d,DEAL_ENTRY)!=DEAL_ENTRY_IN) continue;
         if((ulong)HistoryDealGetInteger(d,DEAL_MAGIC)==magic)
         {
            owned=true;
            string comment=HistoryDealGetString(d,DEAL_COMMENT);
            int k=StringFind(comment,"Ramon:");
            if(k>=0 && StringLen(comment)>=k+22) sample=StringSubstr(comment,k+6,16);
         }
         break;
      }
      HistoryDealSelect(deal);
   }
   if(!owned) return;
   string payload="\"deal_id\":\""+IntegerToString((long)deal)+"\",\"order_id\":\""
      +IntegerToString(HistoryDealGetInteger(deal,DEAL_ORDER))+"\",\"magic\":"
      +IntegerToString(HistoryDealGetInteger(deal,DEAL_MAGIC))
      +",\"entry\":"+IntegerToString(HistoryDealGetInteger(deal,DEAL_ENTRY))
      +",\"reason\":\""+EnumToString((ENUM_DEAL_REASON)HistoryDealGetInteger(deal,DEAL_REASON))+"\""
      +",\"price\":"+DoubleToString(HistoryDealGetDouble(deal,DEAL_PRICE),8)
      +",\"volume\":"+DoubleToString(HistoryDealGetDouble(deal,DEAL_VOLUME),8)
      +",\"profit_units\":"+DoubleToString(HistoryDealGetDouble(deal,DEAL_PROFIT),8)
      +",\"commission_units\":"+DoubleToString(HistoryDealGetDouble(deal,DEAL_COMMISSION),8)
      +",\"swap_units\":"+DoubleToString(HistoryDealGetDouble(deal,DEAL_SWAP),8)
      +",\"comment\":\""+RT_JsonEscape(HistoryDealGetString(deal,DEAL_COMMENT))+"\"";
   RT_Append(magic,small,"deal",payload,sample,pos,HistoryDealGetInteger(deal,DEAL_TIME_MSC));
}
#endif
