#property strict
#property script_show_inputs
#property description "Calculate Eliot XAUUSD_l minimum-volume P/L in account units; no trades."

input string CheckSymbol = "XAUUSD_l";
input double PriceMove = 1.0;

void OnStart()
{
   if(PriceMove<=0 || !SymbolSelect(CheckSymbol,true))
   {
      Print("Invalid symbol or positive price move required");
      return;
   }
   MqlTick quote;
   if(!SymbolInfoTick(CheckSymbol,quote) || quote.bid<=0 || quote.ask<=quote.bid)
   {
      Print("Current broker bid/ask unavailable: ",CheckSymbol);
      return;
   }
   double volume=SymbolInfoDouble(CheckSymbol,SYMBOL_VOLUME_MIN);
   double buyProfit=0.0, sellProfit=0.0;
   ResetLastError();
   if(volume<=0 || !OrderCalcProfit(ORDER_TYPE_BUY,CheckSymbol,volume,
                                    quote.ask,quote.ask+PriceMove,buyProfit)
      || !OrderCalcProfit(ORDER_TYPE_SELL,CheckSymbol,volume,
                          quote.bid,quote.bid-PriceMove,sellProfit))
   {
      Print("OrderCalcProfit failed for minimum volume; error=",GetLastError());
      return;
   }
   Print("ELIOT MONEY CHECK symbol=",CheckSymbol,
         " account_currency=",AccountInfoString(ACCOUNT_CURRENCY),
         " min_volume=",DoubleToString(volume,2),
         " price_move=",DoubleToString(PriceMove,2),
         " buy_profit_account_units=",DoubleToString(buyProfit,4),
         " sell_profit_account_units=",DoubleToString(sellProfit,4));
}
