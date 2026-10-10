#!/usr/bin/env python3
"""Generate RamonTester.mq5 in an isolated output directory, without Win32 DLL imports.

Does not edit the live Ramon.mq5 or Ramon.ex5. Test runs start disarmed.
This removes tester startup's DLL-loading failure; it does NOT enable HTTP
AI forecasts in Strategy Tester, which disallows WebRequest.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import re

IMPORTS = re.compile(
    r'(?m)^#import "user32\.dll"\s*\n.*?^#import\s*$',
    re.DOTALL,
)
CLIPBOARD = re.compile(
    r'(?m)^bool CopyDiagnosticToClipboard\(\)\s*\{.*?^\}\s*(?=datetime NewsHighEventUTC=)',
    re.DOTALL,
)
STUB = """bool CopyDiagnosticToClipboard()
{
   // Tester build: preserve diagnostic generation, never access Win32 DLLs.
   WriteDiagnostic();
   LastCopyStatus="Clipboard unavailable in tester build";
   return false;
}

"""



HARNESS = r"""
// Test-only deterministic two-SSELL risk replay. NEVER issues an order.
// Use only in Strategy Tester (MQL_TESTER) with EnableLiveTrading=false.
input bool TesterRunRiskHarness = false;
input double TesterHistoricalFirstSellRiskUnits = 287.82;
input double TesterHistoricalSecondSellRiskUnits = 292.50;
input double TesterHistoricalPortfolioCapUnits = 300.0;
bool TesterHarnessCompleted=false;
double TesterInjectedRiskUnits=0.0;

void TesterRiskScenario()
{
   if(TesterHarnessCompleted || !TesterRunRiskHarness) return;
   TesterHarnessCompleted=true;
   if(!(bool)MQLInfoInteger(MQL_TESTER) || EnableLiveTrading)
   {
      Print("RAMON_TEST_RISK FAIL wrong execution context"); return;
   }
   if(!EnableV2AccountRiskGuard || MoneyUnitsPerUSD<=0.0)
   {
      Print("RAMON_TEST_RISK FAIL guard disabled or invalid units"); return;
   }
   // The real preflight calculates actual candidate risk from broker specs.
   // A synthetic prior SELL risk is added only in the generated Tester.
   // Use a small valid candidate geometry, then choose injected prior
   // risk so combined exposure crosses the explicitly specified cap.
   double price=SymbolInfoDouble(_Symbol,SYMBOL_BID);
   double point=SymbolInfoDouble(_Symbol,SYMBOL_POINT);
   double min_volume=SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_MIN);
   int stops=(int)SymbolInfoInteger(_Symbol,SYMBOL_TRADE_STOPS_LEVEL);
   double stop=NormalizeDouble(price+MathMax(stops+20,120)*point,(int)SymbolInfoInteger(_Symbol,SYMBOL_DIGITS));
   double broker_pnl=0.0;
   if(price<=0.0 || point<=0.0 || min_volume<=0.0 || stop<=price ||
      !OrderCalcProfit(ORDER_TYPE_SELL,_Symbol,min_volume,price,stop,broker_pnl) || broker_pnl>=0.0)
   {
      Print("RAMON_TEST_RISK INCONCLUSIVE broker candidate geometry invalid"); return;
   }
   // Set only the synthetic historical first position's risk.
   TesterInjectedRiskUnits=TesterHistoricalFirstSellRiskUnits;
   double cap_units=V2MaxPortfolioRiskUSD*MoneyUnitsPerUSD;
   string reason="";
   bool allowed=V2Preflight(ORDER_TYPE_SELL,price,stop,min_volume,reason);
   TesterInjectedRiskUnits=0.0;
   PrintFormat("RAMON_TEST_RISK historical_first=%.2f historical_second=%.2f historical_sum=%.2f cap=%.2f broker_candidate=%.2f allowed=%s reason=%s",
       TesterHistoricalFirstSellRiskUnits,TesterHistoricalSecondSellRiskUnits,
       TesterHistoricalFirstSellRiskUnits+TesterHistoricalSecondSellRiskUnits,
       cap_units,-broker_pnl,allowed?"TRUE":"FALSE",reason);
   if(TesterHistoricalFirstSellRiskUnits+TesterHistoricalSecondSellRiskUnits>cap_units &&
      cap_units<=TesterHistoricalPortfolioCapUnits &&
      !allowed && reason=="portfolio risk cap")
      Print("RAMON_TEST_RISK PASS portfolio cap blocked additional SELL");
   else
      Print("RAMON_TEST_RISK INCONCLUSIVE/FAIL check cap, broker contract and guard result");
}
"""

def create_tester_source(source: str) -> str:
    if source.count('#import "user32.dll"') != 1 or source.count('bool CopyDiagnosticToClipboard()') != 1:
        raise ValueError("Unrecognized source layout: refusing to modify")
    text, imports = IMPORTS.subn("", source)
    if imports != 1:
        raise ValueError(f"Expected exactly one Windows DLL import block, got {imports}")
    text, functions = CLIPBOARD.subn(lambda match: STUB, text)
    if functions != 1:
        raise ValueError(f"Expected exactly one clipboard function, got {functions}")
    if "#import" in text or any(name+"(" in text for name in (
        "OpenClipboard", "GlobalAlloc", "GlobalLock", "GlobalFree",
        "SetClipboardData", "lstrcpyW", "EmptyClipboard",
    )):
        raise ValueError("A DLL import or Win32 invocation remains")
    text, gates = re.subn(
        r'(?m)^input bool EnableLiveTrading\s*=\s*false\s*;',
        "input bool EnableLiveTrading = false;",
        text,
    )
    if gates != 1 or "bool V2Preflight(" not in text:
        raise ValueError("Live-disarmed default or V2 risk preflight is missing")
    # Tester agents may reject FILE_COMMON diagnostic writes (err=5004).
    # Disable only diagnostic file output in the isolated tester build.
    text, diagnostic_flags = re.subn(
        r'(?m)^input bool WriteDiagnosticFile\s*=\s*true\s*;',
        "input bool WriteDiagnosticFile = false;",
        text,
    )
    if diagnostic_flags != 1:
        raise ValueError("Tester diagnostic input not found")
    # Inject synthetic existing risk into actual V2Preflight math.
    # This is strictly a tester-only generated source, not the live EA.
    risk_anchor = "double total=-pnl,symbol_risk=-pnl,direction_risk=-pnl;"
    if text.count(risk_anchor) != 1 or text.count("void OnTick()") != 1:
        raise ValueError("Risk or tick anchors changed; refusing tester generation")
    text = text.replace(risk_anchor, risk_anchor + """
   if((bool)MQLInfoInteger(MQL_TESTER) && TesterRunRiskHarness)
   {
      total+=TesterInjectedRiskUnits;
      symbol_risk+=TesterInjectedRiskUnits;
      direction_risk+=TesterInjectedRiskUnits;
   }
""", 1)
    text = text.replace("void OnTick()\\n{", "void OnTick()\\n{\\n   TesterRiskScenario();", 1)
    text += "\\n" + HARNESS
    # A tester harness must never arm the production EA.
    text = text.replace(
        '#property description "Independent Chronos-2 XAUUSD_l M15 bot; local model server required."',
        '#property description "Ramon Tester ONLY: no Win32 DLL; never attach to live account."',
        1,
    )
    return text


def main():
    p = argparse.ArgumentParser(description=__doc__)
    root = Path(__file__).resolve().parents[1]
    p.add_argument("--source", type=Path, default=root / "mt5/Ramon.mq5")
    p.add_argument("--output", type=Path, default=root / "build/mt5-tester/RamonTester.mq5")
    args = p.parse_args()
    source = args.source.resolve()
    output = args.output.resolve()
    if source == output:
        p.error("Output must differ from source")
    try:
        generated = create_tester_source(source.read_text(encoding="utf-8-sig"))
    except ValueError as exc:
        p.error(str(exc))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(generated, encoding="utf-8")
    print(f"Generated tester-only source: {output}")
    print("No live EA was modified. Build/test this separately in MetaEditor.")


if __name__ == "__main__":
    main()
