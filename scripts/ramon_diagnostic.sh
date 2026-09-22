#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_dir"

echo "=== RAMON MODEL SERVICE ==="
if command -v docker >/dev/null 2>&1; then
  docker compose ps model 2>&1 || true
else
  echo "docker: NOT FOUND"
fi

if command -v curl >/dev/null 2>&1; then
  health="$(curl -fsS --max-time 5 http://127.0.0.1:8012/health 2>&1 || true)"
  if [[ -n "$health" ]]; then
    echo "Health: $health"
  else
    echo "Health: UNREACHABLE"
  fi
else
  echo "curl: NOT FOUND"
fi

echo
echo "=== RAMON MT5 DIAGNOSTIC ==="

if [[ -n "${RAMON_DIAGNOSTIC_FILE:-}" ]]; then
  candidates=("$RAMON_DIAGNOSTIC_FILE")
else
  candidates=()
  for prefix in "${RAMON_WINEPREFIX:-}" "$HOME/.mt5" "$HOME/.wine"; do
    [[ -n "$prefix" && -d "$prefix/drive_c" ]] || continue
    while IFS= read -r -d '' file; do
      candidates+=("$file")
    done < <(
      find "$prefix/drive_c/users" -type f -path '*/AppData/Roaming/MetaQuotes/Terminal/Common/Files/Ramon_Diagnostic.txt' -print0 2>/dev/null
    )
  done
fi

if [[ ${#candidates[@]} -eq 0 ]]; then
  echo "Ramon_Diagnostic.txt not found."
  echo "Attach Ramon v0.13 to XAUUSD_l M15 and wait a few seconds."
  exit 1
fi

newest="${candidates[0]}"
for file in "${candidates[@]}"; do
  [[ -f "$file" ]] || continue
  if [[ "$file" -nt "$newest" ]]; then
    newest="$file"
  fi
done

echo "File: $newest"
echo
cat "$newest"
