#!/usr/bin/env bash
set -u
cd "$(git rev-parse --show-toplevel 2>/dev/null || pwd)"

echo "============================================================"
echo " RAMON PERFORMANCE / LEARNING HEALTH CHECK"
echo "============================================================"
echo "Generated: $(date)"
echo

echo "========== PERFORMANCE =========="
if [ -f scripts/analyze_ramon.sh ]; then
    bash scripts/analyze_ramon.sh --all
else
    echo "FAIL: scripts/analyze_ramon.sh not found"
fi

echo
echo "========== DAILY LEARNING TIMER =========="
systemctl --user status ramon-daily-learning.timer --no-pager 2>/dev/null || echo "WARNING: ramon-daily-learning.timer not available"

echo
echo "========== NEXT / LAST LEARNING =========="
systemctl --user show ramon-daily-learning.timer -p LoadState -p ActiveState -p NextElapseUSecRealtime -p LastTriggerUSec 2>/dev/null || true

echo
echo "============================================================"
echo " LEARNING CHECK FINISHED"
echo "============================================================"
