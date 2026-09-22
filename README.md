# Ramon — independent model-led XAUUSD_l M15 bot

Ramon is separate from Eskandar. A local Python service loads a **real Chronos-2 checkpoint** and forecasts the next four M15 closes from the last 256 completed closes. The EA sends the last 256 completed OHLC bars, receives a BUY/SELL/WAIT decision, and independently checks spread, quote freshness, account, volume risk, margin, existing positions, and daily trade count before placing any order. The model is not replaced by an EMA or RSI rule when unavailable. The initial checkpoint is `autogluon/chronos-2-small` (28M parameters); it is a pretrained time-series model and has not been adapted to this broker until trained below.

The M15 regime is an input for future research, **not a veto**: this robot never calls Eskandar's regime, pullback, or breakout code. WAIT is a valid model decision, with a visible reason.

## Run the real model

Clone this repository on the machine running MT5, with Python 3.11+ and enough RAM for Chronos-2:

```bash
git clone https://github.com/amiradmin/ramon-ai-trader.git
cd ramon-ai-trader
uv sync --extra model --extra dev
uv run --extra model python -m ramon.server --device cpu
```

The first start downloads the actual model weights; a later start loads them from cache. If you use a CUDA GPU, start with `--device cuda`. Verify `http://127.0.0.1:8012/health` reports `ready: true` and the expected model. If loading fails, the service does not start and the EA cannot receive a BUY/SELL signal. Ollama is not used because Chronos-2's native forecasting API is a Python/PyTorch model.

## Install the separate EA

1. In MT5 select **File → Open Data Folder**. Copy `mt5/Ramon.mq5` to that folder's `MQL5/Experts/` directory; compile with F7 in MetaEditor. Use a separate XAUUSD_l M15 chart.
2. MT5 **Tools → Options → Expert Advisors → Allow WebRequest**, add `http://127.0.0.1:8012`. Keep the server on the **same** machine/MT5 Wine host because it listens on loopback only.
3. Start with `EnableLiveTrading=false`. Confirm model name and BUY/SELL/WAIT with the status displayed on the chart and Experts log. No order is sent while disarmed.
4. For the verified LiteFinance cent account only, set the exact `AllowedAccountLogin`, keep `TradeSymbol=XAUUSD_l`, verify `MoneyUnitsPerUSD=100`, and only then enable trading. Inputs default to planned loss at most $0.06 per trade, four entries per broker day, maximum spread 50 points, one position, and a four-bar maximum hold. If the minimum 0.01 lot exceeds the dollar risk, the order is refused and the reason is displayed.

The EA refuses other gold symbols, timeframes, or an account/login mismatch. It also refuses to open alongside another robot's position on the same symbol. It does not place an order when the model is unreachable, returns malformed data, or responds for an older bar. After an entry the broker holds SL/TP; the EA handles the four-bar time exit when connected.

## Evaluate on broker history

The existing `DeepHistorySync` tool in the separate MetaTrader assistant repository can populate `history_bars` in its SQLite store. Export sufficiently deep history for the **exact** `XAUUSD_l` symbol, and pass the real absolute path of that database:

```bash
uv run --extra model python -m ramon.replay \
  --db /absolute/path/to/history.sqlite3 \
  --symbol XAUUSD_l --point 0.01 --fallback-spread 42 --stride 4
```

The replay uses the latest 20% chronologically, enters at the **next** M15 open, holds at most four bars, counts a stop first if both stop and target are crossed within the same candle, and permits only one position at a time. If the store lacks recorded spreads, the explicit fallback is 42 points. This bar-based replay cannot reproduce tick-by-tick fills, margin, latency, or news; validate with broker real-tick data and live cent outcomes before trusting its profitability estimates. MT5 `WebRequest` cannot run in Strategy Tester, which is why offline replay is separate.

## Adapt Chronos-2 to our data

With at least **4000** completed XAUUSD_l M15 bars, LoRA fine-tuning can stage a new checkpoint:

```bash
uv sync --extra model --extra train
uv run --extra model --extra train python -m ramon.train \
  --db /absolute/path/to/history.sqlite3 \
  --symbol XAUUSD_l --device cuda \
  --out ./runtime/chronos/checkpoint-001
```

The oldest 70% trains, the next 10% validates, and the newest 20% remains untouched. A manifest records split boundaries. The service **does not automatically switch** to a newly trained checkpoint: compare the untouched holdout with the original model using the same replay configuration and then start the service with `--model /absolute/path/to/checkpoint-001/model` only if the newer model improves risk-adjusted outcomes. A historical improvement does not guarantee future profit.

## Development

```bash
uv run --extra dev pytest
```

All decision tests inject a fixed forecaster: they check protocol, spread costs, no-trade failures, and replay ordering without downloading weights. An end-to-end model load and MetaEditor compile must also be run on the user's MT5 machine before any armed session.
