#!/usr/bin/env bash
set -euo pipefail

# Read-only Ramon observability/checkpoint runner.
# It does NOT change EA inputs, model thresholds, orders, positions, training labels,
# the SQLite database, or the running model container.
#
# Usage:
#   bash scripts/ramon_checkpoint.sh
#   bash scripts/ramon_checkpoint.sh --force
#
# Optional env:
#   RAMON_CHECKPOINT_DIR=.ramon/checkpoints
#   RAMON_CHECKPOINTS="100 250 500"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

OUT_DIR="${RAMON_CHECKPOINT_DIR:-.ramon/checkpoints}"
CHECKPOINTS="${RAMON_CHECKPOINTS:-100 250 500}"
FORCE=0
[[ "${1:-}" == "--force" ]] && FORCE=1

mkdir -p "$OUT_DIR"
tmp="$(mktemp)"
trap 'rm -f "$tmp"' EXIT

bash scripts/analyze_ramon.sh --all | tee "$tmp"

closed="$(awk -F: '/^Closed trades[[:space:]]*:/ {gsub(/^[[:space:]]+|[[:space:]]+$/, "", $2); print $2; exit}' "$tmp")"
if [[ ! "$closed" =~ ^[0-9]+$ ]]; then
  echo "ERROR: could not parse closed trade count." >&2
  exit 2
fi

target=""
for cp in $CHECKPOINTS; do
  if (( closed >= cp )); then target="$cp"; fi
done

if [[ -z "$target" && "$FORCE" -eq 0 ]]; then
  next=""
  for cp in $CHECKPOINTS; do
    if (( closed < cp )); then next="$cp"; break; fi
  done
  echo
  echo "CHECKPOINT: not due (closed=$closed; next=${next:-none})."
  echo "Use --force for a read-only snapshot now."
  exit 0
fi

label="${target:-manual-$closed}"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
txt="$OUT_DIR/ramon-${label}-${stamp}.txt"
json="$OUT_DIR/ramon-${label}-${stamp}.json"
cp "$tmp" "$txt"

python3 - "$tmp" "$json" "$label" <<'PY'
import json, re, sys
from pathlib import Path

text = Path(sys.argv[1]).read_text(errors="replace")
out = Path(sys.argv[2])
label = sys.argv[3]

def grab(pattern, cast=float):
    m = re.search(pattern, text, re.M)
    return None if not m else cast(m.group(1))

def rolling():
    m = re.search(
        r"^Latest\s+#(\d+)-(\d+)\s+\| PF=\s*([\d.]+) \| WR=\s*([\d.]+)% "
        r"\| expectancy=([+\-\d.]+) units/trade \| avgR=([+\-\d.]+)R \| net=([+\-\d.]+)",
        text, re.M)
    if not m:
        return None
    return {
        "from_trade": int(m.group(1)), "to_trade": int(m.group(2)),
        "profit_factor": float(m.group(3)), "win_rate_pct": float(m.group(4)),
        "expectancy_units": float(m.group(5)), "avg_r": float(m.group(6)),
        "net_units": float(m.group(7)),
    }

risk = re.search(
    r"^override=YES \| trades=\s*(\d+).*?avgRisk=([\d.]+) \| avgRisk/budget=([\d.]+)x",
    text, re.M)

clean = re.search(r"^Clean exit labels:\s*(\d+)/(\d+)", text, re.M)
dd = re.search(r"^Maximum closed-trade DD\s*:\s*([\d.]+) units", text, re.M)
loss_streak = re.search(r"^Max consecutive losses\s*:\s*(\d+) trades", text, re.M)

payload = {
    "schema": 1,
    "checkpoint": label,
    "closed_trades": grab(r"^Closed trades\s*:\s*(\d+)", int),
    "wins": grab(r"^Wins / Losses / BE\s*:\s*(\d+)", int),
    "losses": grab(r"^Wins / Losses / BE\s*:\s*\d+ / (\d+)", int),
    "win_rate_pct": grab(r"^Win rate\s*:\s*([\d.]+)%"),
    "net_units": grab(r"^Net account units\s*:\s*([+\-\d.]+)"),
    "profit_factor": grab(r"^Profit factor\s*:\s*([\d.]+)"),
    "expectancy_units_per_trade": grab(r"^Expectancy/trade\s*:\s*([+\-\d.]+)"),
    "avg_r": grab(r"^Net R / Avg R\s*:\s*[+\-\d.]+R / ([+\-\d.]+)R"),
    "max_closed_dd_units": float(dd.group(1)) if dd else None,
    "max_consecutive_losses": int(loss_streak.group(1)) if loss_streak else None,
    "clean_labels": int(clean.group(1)) if clean else None,
    "clean_label_total": int(clean.group(2)) if clean else None,
    "rolling_20_latest": rolling(),
    "risk_override": None if not risk else {
        "trades": int(risk.group(1)),
        "avg_filled_risk_units": float(risk.group(2)),
        "avg_risk_to_budget_x": float(risk.group(3)),
    },
    "source": "scripts/analyze_ramon.sh --all",
    "read_only": True,
}
out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
print("\n=== RAMON CHECKPOINT SNAPSHOT ===")
print(json.dumps(payload, indent=2, sort_keys=True))
PY

echo
echo "Saved read-only checkpoint:"
echo "  $txt"
echo "  $json"
