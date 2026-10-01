from pathlib import Path
import importlib.util


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "apply_star_ui_to_installed_ea.py"


def load_module():
    spec = importlib.util.spec_from_file_location("star_patcher", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def sample_source() -> str:
    return r'''#property version "1.537"

void Dummy()
{
   string x=""
      +"TradeLearning: "+TradeLearningStatus+"\n"
      +"RoleModels: "+(LastRoleShadow ? "SHADOW (display only)" : (LastEnsembleReady ? "READY" : "LEARNING"))
      +"  ShadowRegime: "+LastShadowRegimeLabel
      +"  ShadowRiskP: "+DoubleToString(LastShadowRiskProbability,3)
      +"TargetLearning: "+(LastTargetLearningActive ? "COLLECTING" : "OFF")
      +"TP1/TP2/TP3 learn: "+DoubleToString(LastTargetTP1,_Digits)
      +"=== V0.50 IMPROVEMENT SHADOWS (OBSERVE ONLY) ===\n"
      +"ShadowPack: "+BoolText(EnableImprovementShadowPack)
      +"DirectionCaution: BUY="+BoolText(ShadowBuyCaution)
      +"ShadowRiskMultiplier: "+DoubleToString(ShadowRiskMultiplier,2)+"x"
      +"ShadowSmallTargetUnits: "+DoubleToString(ShadowSmallTargetUnits,2)
      +"ShadowSmallTPPlan: "+ShadowSmallTPPlan
      +"DeadTradeShadow: "+BoolText(ShadowDeadTrade)
      +"ShadowExecutionEffect: NONE\n\n";

   UiLabel("ROLE_MODELS",(LastRoleShadow ? "SHADOW | "+LastShadowRegimeLabel : "ROLE MODELS "+(LastEnsembleReady ? "READY" : "LEARNING"))
      +" R:"+RoleProbabilityText(LastRegimeProbability)
      +" E:"+RoleProbabilityText(LastEntryProbability)
      +" N:"+RoleProbabilityText(LastNewsProbability)
      +" M:"+RoleProbabilityText(LastMetaProbability)
      +" SL:"+RoleProbabilityText(LastRiskProbability),
      1,1,clrWhite,9);
   ObjectSetString(0,UiPrefix+"ROLE_MODELS",OBJPROP_TOOLTIP,
      "N/A: پیش‌بینی معتبر موجود نیست. درصدها دقت مدل نیستند.");

   UiLabel("NEWS","NEWS "
      +" | model "+(LastNewsModelReady ? "READY" : "LEARNING"),1,1,clrWhite,9);
   cstats[6]=(LastTargetStructureReady ? "READY" : "LEARNING");
   UiLabel("CHECK_NOTE","DISPLAY ONLY - execution logic unchanged.",1,1,clrWhite,9);

   Trade.Buy(0.01,_Symbol);
   Trade.Sell(0.01,_Symbol);
   Trade.PositionClose(1);
   Trade.PositionModify(1,2.0,3.0);
}
'''


def test_star_patcher_is_display_only_and_marks_shadow_learning():
    module = load_module()
    source = sample_source()
    patched, changed = module.patch_source(source)

    assert changed
    assert "*SHADOW*" in patched
    assert "*LEARNING*" in patched
    assert "TargetLearning*" in patched
    assert "*ShadowRiskP:" in patched
    assert "string role_mark=" in patched
    assert '" R"+role_mark+":"' in patched
    assert '" SL"+role_mark+":"' in patched
    assert "* = SHADOW / LEARNING / COLLECTING" in patched

    for call in ("Trade.Buy(", "Trade.Sell(", "Trade.PositionClose(", "Trade.PositionModify("):
        assert patched.count(call) == source.count(call)


def test_star_patcher_is_idempotent():
    module = load_module()
    first, _ = module.patch_source(sample_source())
    second, changed = module.patch_source(first)

    assert second == first
    assert changed == []
