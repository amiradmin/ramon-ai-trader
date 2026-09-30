# External market data and model review — 2026-09-30

These are research candidates, not live replacements. Public listings and model
cards are author claims; inspection depth is stated below. External market bars
cannot supply Ramon's missing executed-trade labels or replace its broker costs.
Keep external imports in a separate database, preserving dataset version, hash,
symbol, clock, point size and provider identity.

## Kaggle links

### XAUUSD & BTCUSD OHLCV: highest-priority external price benchmark

Source: https://www.kaggle.com/datasets/omchoksi04/mt5-ohlcv-historical-dataset-20260825

Version 1 lists CSV/Parquet candle data across MT5 timeframes and CC0 licensing.
The complete `XAUUSD/M15.csv` was downloaded and checked (8,828,186 bytes):

| Check | Observed result |
| --- | --- |
| Rows | 99,966 |
| First / last datetime field | 2022-05-31 16:00 / 2026-08-25 02:15, labeled +0000 |
| Duplicate / non-increasing timestamps | 0 / 0 |
| Invalid or nonpositive OHLC rows | 0 |
| Non-900-second intervals | 1,093; not classified as outages versus session closures |
| Zero or negative spread rows | 0 |
| Spread range | 25–137 in the file's units |
| Zero/nonpositive tick-volume rows | 0 |

SHA256: `ffb47f900eb03d663bf10ed90374ea0f6abb5f3cfb94f51b45f3392eb7ebf282`.
Columns include timestamp, datetime_utc, symbol, timeframe, OHLC, tick_volume,
spread and real_volume. The labeled timezone, broker and spread-to-price point
conversion still need independent verification. Archive and top-level file
copies exist; do not concatenate them as new observations. It cannot repair
13 missing spread observations on the user's September broker history.

### dataset-dqn: sampled data does not match the raw-tick description

Source: https://www.kaggle.com/datasets/omphilevuyomolahloe/dataset-dqn

The card describes Strategy Tester/M1/raw ticks and chronological splits.
The downloaded `Metals/XAUUSD_test.csv` instead has 2,361 rows, DateTime,
OHLC, Volume, Spread and many indicators, with endpoints 2019-11-22 and
2019-12-31. Sample prices around 0.69 look transformed; the first row's
Open exceeds High, so these columns cannot be treated as raw OHLC in one
price scale. This is an observation, not proof of the transformation used.
Bid/Ask tick fields are absent. Recover original prices, scaler provenance
and training-only scaler fitting before considering use. Do not feed this
sample into the current price-level replay. Card license: CDLA-Sharing 1.0.

### Multi-timeframe dataset: label leakage must be separated

Source: https://www.kaggle.com/datasets/olaidegabriel/multi-modal-dataset

The file list includes XAUUSD at 30m, 4h and 1d, plus several currencies and
AAPL. The downloaded `XAUUSD/test_1d_XAUUSD_data.csv` has 446 rows and
explicit `_future_close` and `_future_return` columns. These are potential
targets, never input features. `trend` and every precomputed indicator need
causal provenance before use. Recompute required features from raw OHLC,
using only completed higher-timeframe candles. This is not a direct M15 import.
Card license: ODbL/database-contents terms.

### mT5 dataset: unrelated to financial prices

Source: https://www.kaggle.com/datasets/prachi26pandya/mt5-dataset

The public metadata describes an empathetic-chatbot conversational dataset,
and the file is `T5.csv`. Exclude it from the trading-data shortlist.

## Hugging Face links and forex listings

https://huggingface.co/docs/transformers/en/model_doc/mt5 and
https://huggingface.co/collections/google/mt5-release describe multilingual
text-to-text language models. Their mT5 name does not identify the MetaTrader 5
platform or a price-forecasting model. They are not direct substitutes for Chronos.

### High-Will market data: secondary price research candidate

Source: https://huggingface.co/datasets/High-Will/MetaTrader5-Market-Data

Card and viewer inspected; the full multi-GB file was not downloaded. The card
describes five markets including XAUUSD, seven timeframes and about 19.7M rows.
The displayed 28-column schema has OHLC/volume/indicators but no spread or
Bid/Ask. `zigzag_small` and `zigzag_large` need availability-time provenance;
exclude them from prospective features until confirmed causal. The viewer also
shows equal Bollinger upper/middle/lower values in several sample rows, requiring
inspection of the extraction code rather than trusting derived columns.
Dataset revision inspected: `42e72f7181789494e4be230ce22f6bf1fe21c6bd`.
Official ZigZag documentation explains that later prices can revise prior values:
https://www.mql5.com/en/code/56 .

### Additional candidates from forex listings

Listings inspected:

- https://www.kaggle.com/datasets?search=forex
- https://huggingface.co/models?other=forex
- https://huggingface.co/datasets?other=forex

The forex tag also includes promotional content and datasets for other assets
or horizons. It is a discovery filter, not a validation result.

| Candidate | Potential use and limits |
| --- | --- |
| https://huggingface.co/datasets/Pcitycrypto/xauusd | Card/viewer: minute candles, 2011–2024, about 4.2M rows. Spread was explicitly removed. Useful for historical price-regime research; cannot establish broker-cost net P/L. Full file not audited. |
| https://huggingface.co/datasets/Ehsanrs2/Forex_Factory_Calendar | Card/viewer: 83,427 events, 2007–2025, Asia/Tehran timestamps. Candidate for USD event-window studies, with UTC conversion and point-in-time availability checks. Actual releases and revised Previous values must not leak into pre-release features. |
| https://huggingface.co/lvizcaya/forex-eurusd-direction | Card claims a daily EUR/USD LightGBM/XGBoost ensemble and walk-forward evaluation. Methodology may inform a small tabular baseline; weights and reported accuracy do not transfer directly to XAUUSD M15. Weights not executed. |
| https://huggingface.co/JonusNattapong/AI-XAUUSD-Trading | Card reports daily-data RL, 58.3% wins, $49.45 mean win and $106.18 mean loss. If all three refer to the same binary win/loss trade population, implied mean P/L is 0.583×49.45−0.417×106.18 = −$15.45 before extra costs. This conditional consistency check conflicts with the profitability framing; reconcile trade populations and reproduce results before considering the model. Weights not executed. |

## Application to Ramon

First use the inspected XAUUSD M15 file as a separate historical price benchmark,
after verifying clock and point metadata. It adds older market regimes and
tick-volume inputs, not Ramon's executed outcomes. Retain current broker data
and prospective paired forecasts as the deployment-relevant evaluation period.
Next investigate USD news timing as an independent hypothesis. Any new tabular
or RL model needs chronological evaluation, realistic execution costs and the
actual EA exit policy before it can influence live decisions.
