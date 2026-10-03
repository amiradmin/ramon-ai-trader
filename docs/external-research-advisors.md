# Ramon external research advisors

This document records how third-party XAUUSD/forex resources may be used in Ramon.
The rule is simple: external research can observe and vote, but it cannot execute
orders or silently change live thresholds.

## Current decisions

### Kaggle XAUUSD historical data

Status: **DATASET**

Use: historical M15 benchmark, regime analysis and walk-forward research under the
separate symbol `XAUUSD_KAGGLE`.

Never treat this as LiteFinance execution history.  Spread, candle source and
broker microstructure differ.

### JonusNattapong/xauusd-trading-ai-smc-v2

Status: **FEATURE_SOURCE**

Useful public material:

- M15 XGBoost feature vocabulary
- SMA/EMA/RSI/MACD/Bollinger feature pipeline
- fair-value-gap and order-block research ideas
- M15 feature-importance report

Important audit findings:

- the repository's M15 importance file gives the largest importance to SMA_50,
  EMA_12, BB_lower, EMA_26 and OHLC-derived inputs;
- published M15 FVG/OB features have zero importance in that report;
- the public training script uses a randomized stratified `train_test_split`,
  so its reported validation accuracy is not accepted as walk-forward evidence;
- model files are pickle/joblib artifacts. Ramon does not automatically load
  remote pickle artifacts.

Ramon therefore implements the feature vocabulary locally in
`ramon.smc_features` and will validate any derived shadow model on Ramon's own
chronological folds.

### Romeo V8 super ensemble

Status: **REFERENCE_ONLY**

Useful concepts:

- heterogeneous base learners
- probability calibration
- dynamic model weighting
- meta/stacking layer
- consensus score
- explicit walk-forward / realistic-cost goals

Ramon will reproduce these architectural ideas with its own models and datasets.
Self-reported external performance is not treated as validation.

### PPO gold model

Status: **SHADOW_ONLY**

The public model is a Stable-Baselines3 PPO agent trained for M15 XAUUSD and ships
with normalization state.  Before any inference is accepted Ramon must reproduce:

1. exact observation feature order and scaling;
2. action semantics;
3. VecNormalize state;
4. transaction-cost convention;
5. environment reward definition.

Until then the PPO advisor remains unavailable rather than guessing an input
schema.

### Chartick / Deriv TradersView

Status: **REFERENCE_ONLY**

These can be used manually as external context checks. Ramon must not depend on
website scraping for live execution.

### cTrader XAUUSD robot listings

Status: **REFERENCE_ONLY**

Use only for strategy vocabulary and risk-control ideas (ATR, EMA, VWAP, ADX,
session filters, breakout logic, equity stops). Vendor ROI/PF claims are not
accepted as Ramon validation.

## Shadow vote storage

Research advisors write only to `shadow_votes` in the history database.

A shadow vote records:

- advisor
- BUY / SELL / WAIT
- optional confidence
- regime
- source version
- arbitrary JSON metadata

The table has no code path that authorizes orders.  This makes it possible to
compare Chronos, local SMC research, RL and future ensemble advisors against the
same realized outcomes before promotion.

## Native SMC-inspired features

`ramon.smc_features.smc_technical_features()` computes a deterministic local
feature vector from completed Ramon bars. It includes:

- SMA20 / SMA50
- EMA12 / EMA26
- RSI
- MACD / signal / histogram
- Bollinger bands
- three-candle FVG size/direction
- price-only order-block proxy
- recovery/rejection proxy
- close lags 1/2/3

The order-block feature is intentionally labeled a proxy because the external
source uses volume while the external Ramon history may not contain trustworthy
volume.

## Promotion gate

No external advisor is promoted into Ramon execution unless it passes all of:

1. chronological out-of-sample evaluation;
2. realistic spread/cost assumptions;
3. adequate trade/sample count;
4. positive mean R and PF > 1;
5. consistency across multiple folds;
6. validation on LiteFinance data before live use.
