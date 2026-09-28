// Read-only check of gold contract sizes on the connected MT5 account.
#property script_show_inputs

input double StopPriceDistance = 17.72;
input double HardRiskCapUSD = 0.12;
input double MoneyUnitsPerUSD = 100.0;

void OnStart()
{
   if(StopPriceDistance<=0.0 || HardRiskCapUSD<=0.0 || MoneyUnitsPerUSD<=0.0)
   { Print("Invalid positive input"); return; }
   Print("GOLD CONTRACT AUDIT | symbol | min lot | step | spread points | "
         "stops points | trade mode | min lot risk USD at given stop | under cap");
   for(int i=0;i<SymbolsTotal(false);i++)
   {
      string symbol=SymbolName(i,false);
      if(StringFind(symbol,"XAU")<0 && StringFind(symbol,"GOLD")<0
         && StringFind(symbol,"Gold")<0 && StringFind(symbol,"gold")<0)
         continue;
      if(!SymbolSelect(symbol,true)) continue;
      MqlTick tick;
      double minimum=SymbolInfoDouble(symbol,SYMBOL_VOLUME_MIN);
      double step=SymbolInfoDouble(symbol,SYMBOL_VOLUME_STEP);
      if(!SymbolInfoTick(symbol,tick) || tick.bid<=0.0 || tick.ask<=tick.bid
         || minimum<=0.0 || step<=0.0)
      {
         Print(symbol," | unavailable quote or volume specification");
         continue;
      }
      double buy_loss=0.0,sell_loss=0.0;
      bool buy_ok=OrderCalcProfit(ORDER_TYPE_BUY,symbol,minimum,tick.ask,
                                   tick.ask-StopPriceDistance,buy_loss);
      bool sell_ok=OrderCalcProfit(ORDER_TYPE_SELL,symbol,minimum,tick.bid,
                                    tick.bid+StopPriceDistance,sell_loss);
      if(!buy_ok || !sell_ok || buy_loss>=0 || sell_loss>=0)
      { Print(symbol," | risk calculation unavailable"); continue; }
      double worst_usd=MathMax(-buy_loss,-sell_loss)/MoneyUnitsPerUSD;
      PrintFormat("%s | %.8f | %.8f | %d | %d | %d | %.4f | %s",
                  symbol,minimum,step,
                  (int)SymbolInfoInteger(symbol,SYMBOL_SPREAD),
                  (int)SymbolInfoInteger(symbol,SYMBOL_TRADE_STOPS_LEVEL),
                  (int)SymbolInfoInteger(symbol,SYMBOL_TRADE_MODE),worst_usd,
                  (worst_usd<=HardRiskCapUSD ? "YES" : "NO"));
   }
   Print("Comparison only: verify symbol execution permissions, model compatibility, "
         "spread and stop rules before using any alternative.");
}
