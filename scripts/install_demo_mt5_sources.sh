#!/usr/bin/env bash
# Install demo-compatible Ramon source safely, without touching live settings or restarting Docker.
set -Eeuo pipefail
source_root="${1:-$HOME/Documents/Presentation/ramon-smart-v1}"
mt5_root="$HOME/.mt5/drive_c/Program Files/MetaTrader 5/MQL5"
[[ -f "$source_root/mt5/Ramon.mq5" ]] || { echo "ERROR: Source worktree missing"; exit 2; }
[[ -d "$mt5_root/Experts" ]] || { echo "ERROR: MT5 MQL5 directory not found"; exit 2; }
target="$mt5_root/Experts/Ramon"
mkdir -p "$target"
backup="$source_root/backups/demo-mt5-$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$backup"
for filename in Ramon.mq5 Ramon.ex5 RamonM1M5Collector.mq5 RamonM1M5Collector.ex5; do
  if [[ -e "$target/$filename" ]]; then cp -p "$target/$filename" "$backup/$filename"; fi
done
install -m 0644 "$source_root/mt5/Ramon.mq5" "$target/Ramon.mq5"
install -m 0644 "$source_root/mt5/RamonM1M5Collector.mq5" "$target/RamonM1M5Collector.mq5"
echo "Source files copied to $target"
echo "Backups: $backup (including any prior .ex5 binaries)"
echo "NEXT: Compile both files in MetaEditor (F7). Do NOT enable AutoTrading yet."
echo "EA Inputs demo USD: TradeSymbol empty; AccountIsCent=false; MoneyUnitsPerUSD=1;"
echo "ExpectedAccountCurrency=USD; EnableLiveTrading=false; ConfirmMoneyUnitsPerUSD=false."
