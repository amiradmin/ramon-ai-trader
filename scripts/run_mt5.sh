#!/usr/bin/env bash
set -euo pipefail

prefix="${RAMON_WINEPREFIX:-$HOME/.wine}"
if [[ -n "${RAMON_MT5_DIR:-}" ]]; then
  candidates=("$RAMON_MT5_DIR/terminal64.exe" "$RAMON_MT5_DIR/terminal.exe")
else
  mapfile -d '' -t candidates < <(find "$prefix/drive_c" -maxdepth 9 -type f \
    \( -iname 'terminal64.exe' -o -iname 'terminal.exe' \) -print0 2>/dev/null)
fi

terminals64=()
terminals32=()
for candidate in "${candidates[@]}"; do
  [[ -f "$candidate" ]] || continue
  if [[ "${candidate,,}" == *terminal64.exe ]]; then
    terminals64+=("$candidate")
  else
    terminals32+=("$candidate")
  fi
done
terminals=("${terminals64[@]}")
if [[ ${#terminals[@]} -eq 0 ]]; then terminals=("${terminals32[@]}"); fi
if [[ ${#terminals[@]} -ne 1 ]]; then
  printf 'Found %s MT5 terminal(s); set RAMON_MT5_DIR to the correct installation directory.\n' "${#terminals[@]}" >&2
  printf '%s\n' "${terminals[@]}" >&2
  exit 1
fi
exec wine "${terminals[0]}"
