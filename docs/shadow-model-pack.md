# Ramon shadow-model pack

These models are observation-only. They must not open, block, close, resize, or
otherwise alter a live trade.

## Existing Full-SL Risk

The existing shadow risk role estimates the probability that an otherwise valid
entry finishes at the full broker stop. The API exposes both
`shadow_risk_probability` and the clearer alias
`shadow_full_sl_probability`.

## Direction Quality

Two independent logistic shadow roles are trained from clean real closed trades:

- `buy_quality`: estimated probability of a positive outcome for BUY entries.
- `sell_quality`: estimated probability of a positive outcome for SELL entries.

They reuse the immutable entry/risk feature snapshot and are returned as
`shadow_buy_success_probability` and `shadow_sell_success_probability`.
They are exploratory predictions, not accuracy claims and not execution gates.

## TimesFM 3

`TimesFM3Shadow` is an optional secondary forecast adapter. It is disabled by
default with `RAMON_TIMESFM3_SHADOW_ENABLED=0`. When enabled it reports
direction, quantiles, ATR-normalized move, and agreement with the Chronos
direction. `timesfm3_shadow_effect` is always `NONE`.

The pretrained TimesFM 3 weights currently use a non-commercial/non-production
license. Do not enable the checkpoint for production or commercial trading
unless its license changes or a suitable licensed checkpoint is substituted.

## Training

Retrain the normal shadow bundle as before:

```bash
docker compose --profile tools run --rm tools \
  -m ramon.shadow_roles \
  --db /data/ramon_history.sqlite3 \
  --out /checkpoints/ensemble \
  --symbol XAUUSD_l
```

Direction-quality models become READY only after each side has at least 40 clean
trades with at least 10 wins and 10 losses. Missing models remain explicitly
unavailable and cannot affect execution.
