#!/usr/bin/env bash
set -u
cd "$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
DIAG="$HOME/.mt5/drive_c/users/$USER/AppData/Roaming/MetaQuotes/Terminal/Common/Files/Ramon_Diagnostic.txt"

echo "============================================================"
echo " RAMON TRADING / EXECUTION HEALTH CHECK"
echo "============================================================"
echo "Generated: $(date)"
echo

if [ ! -f "$DIAG" ]; then
    echo "FAIL: Ramon_Diagnostic.txt not found"
    echo "Expected: $DIAG"
    exit 1
fi

echo "========== EA / SIGNAL =========="
grep -E '^(EA version:|Captured:|Symbol:|Market:|Decision:|DecisionID:|TradeLearning:|BaseDecision:|RoleModels:|News:|Signal bar:|EdgeCondition:|StrengthCondition:|IntrabarConfirm:|AITrendConfirm:)' "$DIAG"

echo
echo "========== ACCOUNT / EXECUTION =========="
grep -E '^(Live:|AccountType:|MoneyUnitsConfirmed:|BalanceUnits:|FreeMarginUnits:|Trade permissions:|Status:|Managed position:|ProfitProtection:|ProfitProtectionUnits:|Trades today:)' "$DIAG"

echo
echo "========== RISK / SIZING =========="
grep -E '^(RiskPerTradeUSD:|MinLotOverride:|SizingSide:|PlannedVolume:|EstimatedSLAccountUnits:|MinLotSLAccountUnits:|RiskGate:|MaxSpreadPoints:)' "$DIAG"

echo
echo "============================================================"
echo " TRADING CHECK FINISHED"
echo "============================================================"
