# Inactivity audit — 2026-10-05 16:00 to 2026-10-06 06:28

## Summary

The inactivity was not caused by a complete absence of market opportunities. Ramon evaluated the market repeatedly, generated directional candidates, and then rejected almost all of them in later policy stages.

Observed in the reviewed window:

- 7,867 evaluations.
- 7,861 final WAIT decisions.
- 930 preliminary BUY candidates and 44 preliminary SELL candidates before later-stage vetoes.
- 6 range BUY decisions reached the final decision path while an existing position prevented a new automatic entry.
- A SELL candidate near 06:28 satisfied minimum strength/edge but was vetoed by the volatility-shock guard.

## Counterfactual replay

Replaying every preliminary signal without the policy filters was loss-making, so the correct conclusion is **not** to remove all filters.

The useful ablation was narrower:

- Removing only the market-state veto while keeping later confirmation gates exposed a small set of missed opportunities.
- M1 replay produced three non-overlapping hypothetical entries: two wins and one loss.
- Gross counterfactual result was about +1.33R after the assumed additional execution cost.
- With MAIN-like profit lock / staged target / early-exit behavior approximated, the result fell to about +0.55R.
- Removing the anomaly / volatility protection made the result worse, so that guard remains authoritative.

This is a small sample and is not evidence of general profitability.

## Code change

The live policy previously treated `RANGE_MIDDLE` as an unconditional WAIT state. That meant even a direction agreed by the model, intrabar confirmation, and the independent AI-trend confirmation could never pass.

Policy `market-state-v2` changes only that case:

- `RANGE_MIDDLE` routes to `CONFIRMED_MODEL`.
- Both BUY and SELL are structurally eligible there.
- The final policy still requires:
  - the model decision,
  - intrabar confirmation,
  - independent AI-trend confirmation,
  - matching directions,
  - no range-reversal execution flag.
- LOW_LIQUIDITY, FLAT_MARKET, PRICE_GAP, VOLATILITY_SHOCK, DISORDERLY_MARKET, VOLATILITY_COMPRESSION, REGIME_TRANSITION, CONFLICTING_STRUCTURE and UNCERTAIN remain WAIT.

The goal is to remove one hard veto that was demonstrably suppressing candidate entries without weakening the hazard guards that protected the replay.

## Git commits

- `acbcd55b1b58aa399461b91db15cbf082cb483ef` — stop hard-vetoing confirmed RANGE_MIDDLE entries.
- `8464f8c2fe53770dbeb212f0ed7e09b3719ed42b` — add test coverage for confirmed RANGE_MIDDLE routing.
- `fb3de7c7510efc816d0f940428c4bdfdc79ba463` — fix test parametrization.

## Deployment

This change is Python service policy only. Ramon.mq5 was not changed. Rebuild/restart the Docker service after pulling main; no MetaEditor compile is required for this change.
