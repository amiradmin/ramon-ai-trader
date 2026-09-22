#!/usr/bin/env bash
# Install/load Chronos-2, then copy and compile Ramon in the selected MT5 terminal.
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_dir"

mode="${1:-full}"
if [[ $# -gt 1 || "$mode" != "full" && "$mode" != "--diagnose" && "$mode" != "--model-only" && "$mode" != "--mt5-only" ]]; then
  printf '%s\n' 'Usage: bash scripts/setup_local.sh [--diagnose|--model-only|--mt5-only]' >&2
  exit 2
fi

wine_roots=()
for root in "${RAMON_WINEPREFIX:-}" "$HOME/.mt5" "$HOME/.wine"; do
  [[ -n "$root" && -d "$root" ]] || continue
  duplicate=false
  for existing in "${wine_roots[@]}"; do
    if [[ "$existing" == "$root" ]]; then duplicate=true; break; fi
  done
  if [[ "$duplicate" == false ]]; then wine_roots+=("$root"); fi
done

resolve_mt5() {
  local candidate root candidate_dir existing seen
  local -a editors=() editor_dirs=() data_dirs=()
  if [[ -n "${RAMON_MT5_DIR:-}" ]]; then
    mt5_dir="$RAMON_MT5_DIR"
    if [[ -f "$mt5_dir/metaeditor64.exe" ]]; then
      metaeditor="$mt5_dir/metaeditor64.exe"
    elif [[ -f "$mt5_dir/metaeditor.exe" ]]; then
      metaeditor="$mt5_dir/metaeditor.exe"
    else
      printf 'RAMON_MT5_DIR has no metaeditor64.exe or metaeditor.exe: %s\n' "$mt5_dir" >&2
      return 1
    fi
  else
    for root in "${wine_roots[@]}"; do
      while IFS= read -r -d '' candidate; do
        editors+=("$candidate")
        candidate_dir="$(dirname "$candidate")"
        seen=false
        for existing in "${editor_dirs[@]}"; do
          if [[ "$existing" == "$candidate_dir" ]]; then seen=true; break; fi
        done
        if [[ "$seen" == false ]]; then editor_dirs+=("$candidate_dir"); fi
      done \
        < <(find "$root/drive_c" -maxdepth 9 -type f \
          \( -iname 'metaeditor64.exe' -o -iname 'metaeditor.exe' \) -print0 2>/dev/null)
    done
    if [[ ${#editor_dirs[@]} -ne 1 ]]; then
      printf 'Found %s MetaEditor installation(s) in the usual Wine prefixes:\n' "${#editor_dirs[@]}" >&2
      if [[ ${#editors[@]} -gt 0 ]]; then printf '  %s\n' "${editors[@]}" >&2; fi
      printf '%s\n' 'Set RAMON_MT5_DIR to the installation directory containing metaeditor64.exe.' >&2
      return 1
    fi
    mt5_dir="${editor_dirs[0]}"
    if [[ -f "$mt5_dir/metaeditor64.exe" ]]; then
      metaeditor="$mt5_dir/metaeditor64.exe"
    else
      metaeditor="$mt5_dir/metaeditor.exe"
    fi
  fi

  if [[ -n "${RAMON_MT5_DATA_DIR:-}" ]]; then
    mt5_data_dir="$RAMON_MT5_DATA_DIR"
    if [[ ! -d "$mt5_data_dir/MQL5/Experts" ]]; then
      printf 'RAMON_MT5_DATA_DIR has no MQL5/Experts: %s\n' "$mt5_data_dir" >&2
      return 1
    fi
  elif [[ -d "$mt5_dir/MQL5/Experts" ]]; then
    mt5_data_dir="$mt5_dir"
  else
    for root in "${wine_roots[@]}"; do
      while IFS= read -r -d '' candidate; do data_dirs+=("$(dirname "$(dirname "$candidate")")"); done \
        < <(find "$root/drive_c" -maxdepth 16 -type d -path '*/MQL5/Experts' -print0 2>/dev/null)
    done
    if [[ ${#data_dirs[@]} -ne 1 ]]; then
      printf 'Found %s MT5 data folder candidate(s):\n' "${#data_dirs[@]}" >&2
      if [[ ${#data_dirs[@]} -gt 0 ]]; then printf '  %s\n' "${data_dirs[@]}" >&2; fi
      printf '%s\n' 'In MT5 use File > Open Data Folder, then set RAMON_MT5_DATA_DIR to that path.' >&2
      return 1
    fi
    mt5_data_dir="${data_dirs[0]}"
  fi

  wine_prefix="${RAMON_WINEPREFIX:-}"
  if [[ -z "$wine_prefix" ]]; then
    for root in "${wine_roots[@]}"; do
      if [[ "$metaeditor" == "$root/"* ]]; then wine_prefix="$root"; break; fi
    done
  fi
  if [[ -z "$wine_prefix" ]]; then
    printf '%s\n' 'Set RAMON_WINEPREFIX to the prefix used to launch MetaTrader.' >&2
    return 1
  fi
  printf 'MetaEditor: %s\nMT5 data: %s\nWine prefix: %s\n' "$metaeditor" "$mt5_data_dir" "$wine_prefix"
}

if [[ "$mode" == "--diagnose" ]]; then
  resolve_mt5
  exit $?
fi

if [[ "$mode" != "--mt5-only" ]] && ! command -v uv >/dev/null 2>&1; then
  printf '%s\n' 'uv is required: https://docs.astral.sh/uv/getting-started/installation/' >&2
  exit 1
fi

if [[ "$mode" == full || "$mode" == "--mt5-only" ]]; then
  resolve_mt5
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
fi

if [[ "$mode" != "--mt5-only" ]]; then
  printf '%s\n' 'Installing Python model dependencies (PyTorch may be a large download)...'
  uv sync --extra model --extra dev
  printf '%s\n' 'Downloading/loading Chronos-2 and running a real four-step forecast...'
  uv run --extra model python -m ramon.preflight --device "${RAMON_DEVICE:-cpu}"
fi

if [[ "$mode" == "--model-only" ]]; then
  printf '%s\n' 'Real-model preflight passed. MT5 Expert was not installed; run --diagnose and then the full setup.'
  exit 0
fi

target_dir="$mt5_data_dir/MQL5/Experts/Ramon"
mkdir -p "$target_dir"
target="$target_dir/Ramon.mq5"
if [[ -f "$target" ]] && ! cmp -s "$project_dir/mt5/Ramon.mq5" "$target"; then
  cp -p "$target" "$target.bak.$(date +%Y%m%d%H%M%S)"
fi
cp "$project_dir/mt5/Ramon.mq5" "$target"
win_target="$(WINEPREFIX="$wine_prefix" winepath -w "$target")"
printf 'Compiling %s\n' "$target"
if ! WINEPREFIX="$wine_prefix" "$wine_command" "$metaeditor" "/compile:$win_target" /log; then
  printf 'MetaEditor failed: %s\n' "$metaeditor" >&2
  if command -v file >/dev/null 2>&1; then file "$metaeditor" >&2; fi
  printf '%s\n' 'If Wine reported missing wine32 and the file is PE32, inspect the i386 Wine package before installing it.' >&2
  printf '%s\n' 'Also read Ramon.log in the installed EA directory, if it exists.' >&2
  exit 1
fi
if [[ ! -s "$target_dir/Ramon.ex5" || "$target" -nt "$target_dir/Ramon.ex5" ]]; then
  printf '%s\n' 'Compile not verified: a fresh Ramon.ex5 was not produced; read Ramon.log.' >&2
  exit 1
fi
printf 'Ready: %s\n' "$target_dir/Ramon.ex5"
printf '%s\n' 'Trading remains DISARMED. Start the model server, allow the WebRequest URL and attach Ramon to XAUUSD_l M15.'
