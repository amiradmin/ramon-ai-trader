#!/usr/bin/env bash
# Install dependencies, run real Chronos-2 inference, then compile a disarmed MT5 EA.
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_dir"

if ! command -v uv >/dev/null 2>&1; then
  printf '%s\n' 'uv is required: https://docs.astral.sh/uv/getting-started/installation/' >&2
  exit 1
fi

mt5_dir="${RAMON_MT5_DIR:-$HOME/.mt5/drive_c/Program Files/MetaTrader 5}"
mt5_data_dir="${RAMON_MT5_DATA_DIR:-$mt5_dir}"
mt5_experts="$mt5_data_dir/MQL5/Experts"
metaeditor="$mt5_dir/metaeditor64.exe"
if [[ ! -f "$metaeditor" ]]; then metaeditor="$mt5_dir/metaeditor.exe"; fi
if [[ ! -d "$mt5_experts" || ! -f "$metaeditor" ]]; then
  printf 'MT5 installation or data directory not found: %s | %s\nSet RAMON_MT5_DIR to the directory with metaeditor64.exe and RAMON_MT5_DATA_DIR to the terminal data directory with MQL5/Experts.\n' "$mt5_dir" "$mt5_data_dir" >&2
  exit 1
fi
if command -v wine >/dev/null 2>&1; then
  wine_command=wine
elif command -v wine64 >/dev/null 2>&1; then
  wine_command=wine64
else
  printf '%s\n' 'Wine is required to compile the MT5 Expert Advisor.' >&2
  exit 1
fi
if ! command -v winepath >/dev/null 2>&1; then
  printf '%s\n' 'winepath is required to pass the EA file to MetaEditor.' >&2
  exit 1
fi

printf '%s\n' 'Installing Python model dependencies (PyTorch may be a large download)...'
uv sync --extra model --extra dev
printf '%s\n' 'Downloading/loading Chronos-2 and running a real four-step forecast...'
uv run --extra model python -m ramon.preflight --device "${RAMON_DEVICE:-cpu}"

target_dir="$mt5_experts/Ramon"
mkdir -p "$target_dir"
target="$target_dir/Ramon.mq5"
cp "$project_dir/mt5/Ramon.mq5" "$target"
win_target="$(WINEPREFIX="${RAMON_WINEPREFIX:-$HOME/.mt5}" winepath -w "$target")"
printf 'Compiling %s\n' "$target"
if ! WINEPREFIX="${RAMON_WINEPREFIX:-$HOME/.mt5}" "$wine_command" "$metaeditor" "/compile:$win_target" /log; then
  printf '%s\n' 'MetaEditor failed; see Ramon.log in the installed EA directory.' >&2
  exit 1
fi
if [[ ! -s "$target_dir/Ramon.ex5" || "$target" -nt "$target_dir/Ramon.ex5" ]]; then
  printf '%s\n' 'Compile not verified: a fresh Ramon.ex5 was not produced; read Ramon.log.' >&2
  exit 1
fi
printf 'Ready: %s\n' "$target_dir/Ramon.ex5"
printf '%s\n' 'Trading remains DISARMED. Start the model server, add the WebRequest URL in MT5 and attach Ramon to an XAUUSD_l M15 chart.'
