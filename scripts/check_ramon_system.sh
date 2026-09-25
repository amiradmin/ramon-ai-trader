#!/usr/bin/env bash
set -u
cd "$(git rev-parse --show-toplevel 2>/dev/null || pwd)"

echo "============================================================"
echo " RAMON SYSTEM HEALTH CHECK"
echo "============================================================"
echo "Generated: $(date)"
echo
echo "========== 1. GIT =========="
echo "Branch:"
git branch --show-current
echo
echo "Status:"
git status --short
echo
echo "Last commits:"
git log -3 --oneline
echo
echo "========== 2. DOCKER =========="
docker compose ps
echo
echo "========== 3. MODEL HEALTH =========="
curl -s --max-time 10 http://127.0.0.1:8012/health
echo
echo
echo "========== 4. SOURCE EA VERSION =========="
grep -n '#property version' mt5/Ramon.mq5
echo
echo "============================================================"
echo " SYSTEM CHECK FINISHED"
echo "============================================================"
