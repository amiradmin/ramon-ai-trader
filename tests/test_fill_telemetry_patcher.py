from pathlib import Path
import importlib.util


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "apply_fill_telemetry_to_installed_ea.py"


def load_module():
    spec = importlib.util.spec_from_file_location("fill_patcher", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def sample_source() -> str:
    return r'''#property version "1.537"

bool ClosedTradePayload(const ulong identifier,string &payload)
{
   string sample="",direction="",exit_reason="";
   datetime opened=0,closed=0;
   ulong opening_deal=0,closing_deal=0;
   double in_volume=0.0,out_volume=0.0,net=0.0,risk=0.0;
   double profit=0.0,commission=0.0,swap=0.0,fee=0.0;
   for(int i=0;i<1;i++)
   {
      ulong deal=1;
      long entry=DEAL_ENTRY_IN;
      double volume=0.01;
      if(entry==DEAL_ENTRY_IN)
      {
         in_volume+=volume;
         double fill=HistoryDealGetDouble(deal,DEAL_PRICE);
         double loss=0.0;
      }
   }
   if(sample=="" || risk<=0.0 || closed<opened || in_volume<=0.0
      || MathAbs(in_volume-out_volume)>0.000001) return false;
   net=profit+commission+swap+fee;
   payload="{\"trade_key\":\"x\""
      +",\"net_units\":"+DoubleToString(net,8)+",\"initial_risk_units\":"+DoubleToString(risk,8)
      +",\"profit_units\":"+DoubleToString(profit,8);
   return true;
}

void Other()
{
   Trade.Buy(0.01,_Symbol);
   Trade.Sell(0.01,_Symbol);
   Trade.PositionClose(1);
   Trade.PositionModify(1,2.0,3.0);
}
'''


def test_fill_patcher_is_telemetry_only_and_idempotent():
    module = load_module()
    source = sample_source()
    patched = module.patch_source(source)

    assert "entry_fill_value+=fill*volume;" in patched
    assert "actual_fill_price=entry_fill_value/in_volume" in patched
    assert '\\\"actual_fill_price\\\"' in patched
    assert '#property version "1.537"' in patched

    for call in ("Trade.Buy(", "Trade.Sell(", "Trade.PositionClose(", "Trade.PositionModify("):
        assert patched.count(call) == source.count(call)

    assert module.patch_source(patched) == patched
