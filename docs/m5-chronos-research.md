# Ramon M5: Chronos 2 short-trade research

Goal: evaluate roughly USD 0.05 **net** per winning trade on the CENT account,
with USD 0.50 as a cumulative milestone, not an order quota or guaranteed result.

The live EA and model server currently require M15. Changing the chart to M5
does not turn their signals into M5 signals. No M5 live execution is enabled
by the data export changes in this branch.

## Collect closed M5 history

In MetaEditor compile `mt5/ExportRamonHistory.mq5`. Run the script once with:

- `ExportSymbol = XAUUSD_l`
- `ExportTimeframe = PERIOD_M5`
- `BarsToExport = 10000` or more, when available
- `OutputFileName = Ramon_XAUUSD_l_M5_History.csv`

Wait for `Ramon history export complete PERIOD_M5` in the Experts tab.
From the repository root, import the file into the existing database, where
M5 and M15 rows are stored separately:

```bash
CSV="$HOME/.mt5/drive_c/users/$USER/AppData/Roaming/MetaQuotes/Terminal/Common/Files/Ramon_XAUUSD_l_M5_History.csv"
test -s "$CSV" || { echo "M5 CSV missing: $CSV" >&2; exit 1; }
mkdir -p data/imports
cp -- "$CSV" data/imports/Ramon_XAUUSD_l_M5_History.csv
docker compose --profile tools run --rm --no-deps tools -m ramon.import_history \
  /data/imports/Ramon_XAUUSD_l_M5_History.csv \
  --db /data/ramon_history.sqlite3 --symbol XAUUSD_l --timeframe M5
```

## Before an M5 live EA

1. Run Chronos 2 on a separately keyed M5 context and forecast horizon.
2. Build and validate role-model features and labels on M5 data only. Do not
   reuse the trained M15 role bundle on M5.
3. Replay chronological, untouched M5 periods with recorded spread and
   conservative stop-first ordering for same-bar target/stop touches. Evaluate
   net return, drawdown and win rate; candle data cannot prove tick fill order.
4. Size using `OrderCalcProfit` for the broker's minimum volume. Reject entry
   when a legally placed stop exceeds the USD 0.12 risk cap; do not raise the
   cap just to obtain a five-cent take profit. Verify execution in MT5 tester.
5. Deploy the M5 system independently of the existing M15 EA after validation.

For context, USD 0.05 gains versus USD 0.12 losses need more than 70.6% wins
before costs. A net five-cent target needs to account for spread, commission,
slippage and other fees. The historical M5 spread is only a bar-level proxy.
