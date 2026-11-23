# IMPULSE_CONTINUATION: isolated M15 research candidate

## Goal

Explore the subset of TREND_PULLBACK's `IMPULSE_STILL_EXTENDING` observations where a distinct **trend-continuation breakout** might have positive out-of-sample expectancy. Counts of evaluations are **not counts of independent opportunities or trades**, and this module does not automatically buy or sell on that rejection reason.

## Scope

`ea_bridge/impulse_continuation_research.py` is a standalone counterfactual replay module. It is not in `STRATEGIES`, has no worker, no command creation, and **cannot authorize real trades**.

When the existing M15 research replay includes `TREND_PULLBACK`, its JSON includes `impulse_continuation_research` beside `strategies`. The original trend-pullback result is unchanged. No dashboard or checkbox is added in this first research phase.

## Explicit, tentative rules

- Closed M15 breakout beyond the preceding 12-candle high/low with 20/50 simple-mean trend agreement.
- Candle range between 0.7 and 2.2 times rolling 14-bar ATR, body at least half the candle range.
- Modeled spread at most 12% ATR and structural SL based on preceding six candles with 0.2 ATR buffer; stop distance 0.6 to 3 ATR.
- Target 1.5R, fill at *next* M15 open with modeled spread and adverse per-side slippage.
- Independent one-position-at-a-time sequence; minimum broker volume and 0.5%-or-lower risk cap. Never force a minimum lot above budget.
- OHLC ambiguities resolve STOP first, adverse gap-through stop price, maximum 12-bar holding time. Historical missing-bar gaps reset warmup; if a position crosses a gap, research halts as unresolved.
- Signal window deduplication by 12 M15 candles; reported evaluations are not interpreted as independent trades.

## Run and inspect

```bash
docker compose exec django python manage.py test ea_bridge.test_impulse_continuation_research ea_bridge.test_replay
```

Run your existing M15 Historical Replay selecting TREND_PULLBACK, with explicit XAUUSD_l data source, balance, spread, tick size/value, and volume constraints. Export the JSON and inspect the `impulse_continuation_research` field. If that field is missing, the code is not deployed.

## Evidence and release gate

This is **descriptive research**, not a proven edge or an implementation of the original trend-pullback detector. It uses a new breakout hypothesis, not the exact 1,019 historical strategy rejection events. To establish overlap with `IMPULSE_STILL_EXTENDING`, the next iteration should record per-bar original pipeline reasons and correlate them by bar timestamp and setup direction.

Do not promote to live execution without frozen in/out-of-sample data, several non-overlapping months/regimes, realistic symbol-specific cent-contract economics, spread and slippage stress, drawdown and margin stress, strategy collision checks, validated feed continuity, and broker/demo execution tests. A profit factor without losses, a small sample, or flagged data gaps is **not evidence of profit**.

**No setting in this change enables trades.** Keep the current TREND_PULLBACK live controls unchanged.
