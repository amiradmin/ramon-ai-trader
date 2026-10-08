# Conservative AI entry policy — 2026-10-08

## Evidence
Uploaded direction trace shows five selected losses (#413, #415, #416, #424, #426): base WAIT, intrabar_confirmed=0, ai_trend_confirmed=0, entry_timing_ready=0. AI Engine V2 replaced WAIT with BUY/SELL. This does not prove wrong directional classification.

## Switch
RAMON_AI_ENGINE_V2_CONSERVATIVE_POLICY=1 enables only the AI Engine V2 gates; default 0 is OFF and preserves prior live behavior.

1. A base WAIT is preserved unless both intrabar and AI trend confirmations are present.
2. Entry timing must be ready.
3. Confirmed opposing intrabar or AI trend votes veto the candidate.

Range/reversal paths are evaluated upstream and do not inherit this opt-in guard.

## Replay scope and limitations
Counterfactual against the five selected losses: 5/5 would become WAIT (zero entries). This is NOT a representative profit-factor or expectancy evaluation, because winning and losing entries must both be replayed under identical costs. Do not use this five-loss result as evidence of profitable improvement. Run full purged out-of-sample decision and execution replay, comparing blocked winners and blocked losers before enabling on a live account.

## Telemetry question
decision_audit.settings.require_direction_confirmation=false whereas inference provenance settings showed true. Investigate effective settings logging separately.

## Run tests
uv run pytest -q tests/test_conservative_ai_policy.py
