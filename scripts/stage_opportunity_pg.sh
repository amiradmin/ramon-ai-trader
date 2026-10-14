#!/usr/bin/env bash
# Stage the market opportunity engine into a PostgreSQL canary checkout, preserving
# the locally customized server.py. Does not enable automatic order execution.
set -Eeuo pipefail
ROOT="${1:-$HOME/Documents/Presentation/ramon-ai-trader}"
cd "$ROOT"
SOURCE=/tmp/ramon-smart-v1
test -f "$SOURCE/src/ramon/opportunity_execution.py"
test -f "$SOURCE/src/ramon/market_opportunity_engine.py"
test -f src/ramon/server.py
test -f compose.pg-all-staging.yaml
test -f docker-compose.v3-guard.yml
mkdir -p backups/opportunity-engine
restore_on_error() {
  cp "backups/opportunity-engine/server.py.$STAMP" src/ramon/server.py
  for f in market_opportunity_engine.py opportunity_execution.py; do
    if test -f "backups/opportunity-engine/$f.$STAMP"; then
      cp "backups/opportunity-engine/$f.$STAMP" "src/ramon/$f"
    else
      rm -f "src/ramon/$f"
    fi
  done
  echo "ERROR: original files restored; no Docker restart performed" >&2
}
trap restore_on_error ERR
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
cp src/ramon/server.py "backups/opportunity-engine/server.py.$STAMP"
for f in market_opportunity_engine.py opportunity_execution.py; do
  if test -e "src/ramon/$f"; then cp "src/ramon/$f" "backups/opportunity-engine/$f.$STAMP"; fi
  cp "$SOURCE/src/ramon/$f" "src/ramon/$f"
done
PYTHONPATH=src python3 - "$STAMP" <<'PY'
from pathlib import Path
import sys
p=Path("src/ramon/server.py")
source=p.read_text()
if "from .opportunity_execution import apply_live_opportunity" not in source:
    anchor="from .ai_decision_engine import apply_ai_decision_engine"
    if source.count(anchor)!=1:
        raise SystemExit("Server import anchor mismatch; restore backup")
    source=source.replace(anchor,anchor+"\nfrom .market_opportunity_engine import detect_opportunity\nfrom .opportunity_execution import apply_live_opportunity")
block='''                # Structure opportunity execution bridge (independent of V2 WAIT).
                # Disable automatically unless explicitly opted in.
                opportunity = detect_opportunity(market)
                response.update({"opportunity_engine_version": "structure-v1",
                    **{"opportunity_" + k: v for k,v in opportunity.to_dict().items()}})
                apply_live_opportunity(
                    response, opportunity,
                    enabled=os.environ.get("RAMON_STRUCTURE_LIVE_ENABLED", "0") == "1",
                    ea_capable=payload.get("range_execution_ready") is True,
                    fresh_quote=quote_time is not None,
                    assessment=market_assessment,
                    max_spread_ok=(market.ask-market.bid)/market.point <= settings.max_spread_points,
                    manual_override=bool(active_overrides),
                )
'''
if 'opportunity = detect_opportunity(market)' not in source:
    anchor='                sample_key = uuid4().hex[:16]'
    if source.count(anchor)!=1:
        raise SystemExit("Decision insertion anchor mismatch; restore backup")
    source=source.replace(anchor,block+anchor)
p.write_text(source)
PY
if ! python3 -m py_compile src/ramon/server.py src/ramon/market_opportunity_engine.py src/ramon/opportunity_execution.py; then
  cp "backups/opportunity-engine/server.py.$STAMP" src/ramon/server.py
  echo "Compilation failed; server restored. Existing container not touched."
  exit 1
fi
(
  cd "$SOURCE"
  PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run --offline --extra dev python -m pytest -q \
    tests/test_market_opportunity_engine.py tests/test_opportunity_execution.py
)
# Preserve existing overlay; do not publish any automatic order authority on
# an account merely because unit tests passed.
cat > docker-compose.opportunity-staged.yml <<'YAML'
services:
  model:
    environment:
      RAMON_STRUCTURE_LIVE_ENABLED: "0"
YAML
docker compose -f compose.yaml -f compose.pg-all-staging.yaml \
  -f docker-compose.v3-guard.yml -f docker-compose.opportunity-staged.yml config --quiet
echo "PASS: integration staged; automatic live opportunity orders DISABLED."
echo "Review the MT5 EA execution path and position exposure before any activation."
echo "Original server backup: backups/opportunity-engine/server.py.$STAMP"
