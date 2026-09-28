# Eliot M5: Chronos 2 short-trade research

Eliot is the proposed separate M5 robot. Goal: evaluate roughly USD 0.05
**net** per winning trade on the CENT account,
with USD 0.50 as a cumulative milestone, not an order quota or guaranteed result.

The live Ramon EA and model server currently require M15. Changing the chart to M5
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
docker compose --profile tools build tools
CSV="$HOME/.mt5/drive_c/users/$USER/AppData/Roaming/MetaQuotes/Terminal/Common/Files/Ramon_XAUUSD_l_M5_History.csv"
test -s "$CSV" || { echo "M5 CSV missing: $CSV" >&2; exit 1; }
mkdir -p data/imports
cp -- "$CSV" data/imports/Ramon_XAUUSD_l_M5_History.csv
docker compose --profile tools run --rm --no-deps tools -m ramon.import_history \
  /data/imports/Ramon_XAUUSD_l_M5_History.csv \
  --db /data/ramon_history.sqlite3 --symbol XAUUSD_l --timeframe M5
```

The tools image bundles project code at build time. Rebuild `tools` after
pulling this branch; `docker compose run` alone uses the previous image.
Rebuilding the shared image tag does not recreate the running M15 model
container; leave that container running while evaluating Eliot offline.

## Offline Chronos 2 probe

After the import, run the model on the untouched final fifth of the M5 bars:

```bash
docker compose --profile tools run --rm --no-deps tools -m ramon.eliot_research \
  --db /data/ramon_history.sqlite3 --symbol XAUUSD_l \
  --model autogluon/chronos-2-small --device cpu \
  --units-per-price 1 --max-decisions 100
```

If the 256-bar uninterrupted M5 context leaves too few decisions after
session gaps, measure a shorter context separately (this changes Chronos
inputs, so its forecasts cannot be compared as the same strategy):

```bash
docker compose --profile tools run --rm --no-deps tools -m ramon.eliot_research \
  --db /data/ramon_history.sqlite3 --symbol XAUUSD_l \
  --model autogluon/chronos-2-small --device cpu \
  --units-per-price 1 --max-decisions 500 --context-bars 64
```

The report includes skipped-bar counts and the median, 90th percentile, and
maximum forecast moves alongside the required entry move. Do not choose the
context length or lower the entry threshold using the final fifth and then
claim that same period is an untouched out-of-sample test.

## Eliot M5 learned roles

To train three independent M5 roles (market regime, direction, and entry)
on the first 70% of closed bars and compare them alongside Chronos 2 on
the next 10% and final 20%, run:

```bash
docker compose --profile tools build tools
docker compose --profile tools run --rm --no-deps tools -m ramon.eliot_roles \
  --db /data/ramon_history.sqlite3 --symbol XAUUSD_l \
  --model autogluon/chronos-2-small --device cpu --units-per-price 1
```

The role checkpoints are written only to `data/eliot_models/`. They are not
the Ramon M15 role bundle. The output reports each chronological period's
Chronos-only relaxed direction probe next to the same candidate signals
filtered by the three trained roles. A Chronos direction probe requires
at least 1.0 price move and is a separate experiment from the conservative
5.42-price-unit entry rule above. If the trained models reject everything,
that is an honest zero-trade outcome. Neither result authorizes M5 live orders.
The last 20% was already inspected in the Chronos-only experiment, so its
result is exploratory; collect fresh later data for a clean future test.

## Freeze Chronos-aligned roles for fresh bars

The original M5 roles trained on all bars and rejected every signal. The
forward experiment trains three different labels **only for Chronos candidate
signals**: realized positive account-unit return, TP-first, and SL-first.
The candidate features include historical volatility, signed price changes,
spread, and Chronos' predicted movement and uncertainty. Its thresholds are
fixed before new bars arrive. Historical training results are not a profit
claim; the old holdout has already been used in earlier investigations.

Freeze the currently imported 10,000-bar dataset by specifying its exact last
completed M5 timestamp. Do this once, on the existing Eliot branch:

```bash
docker compose --profile tools run --rm --no-deps tools -m ramon.eliot_forward fit \
  --db /data/ramon_history.sqlite3 --symbol XAUUSD_l \
  --model autogluon/chronos-2-small --device cpu \
  --units-per-price 1 --cutoff-time 1790613000
```

The frozen bundle is stored in `data/eliot_forward/`, entirely separate from
Ramon. The fit command refuses to overwrite an existing bundle. A later
`evaluate` call without newer completed bars says `AWAITING_NEW_M5_BARS`.
After new trading sessions, export M5 history again with the same script and
filename, import the CSV as above (existing timestamps update, new timestamps
append), and run:

```bash
docker compose --profile tools run --rm --no-deps tools -m ramon.eliot_forward evaluate \
  --db /data/ramon_history.sqlite3 --symbol XAUUSD_l \
  --model autogluon/chronos-2-small --device cpu --units-per-price 1
```

Only decisions **after** the frozen timestamp appear in this comparison.
The M5 candle data still cannot show actual broker order fills, and role
training does not turn Eliot into a live trading EA. Accumulate enough fresh
bars and actual candidate trades before judging the model.

## Broker ticks and account-unit conversion

Compile `mt5/CheckEliotMoneyUnits.mq5` and `mt5/ExportEliotTicks.mq5` in
MetaEditor (Scripts folder). Run the money-check script on the **same account**
and `XAUUSD_l`. Copy its `ELIOT MONEY CHECK` Experts line. The printed buy and
sell profits are **account currency units** for the broker minimum volume and
a price move of 1.0; check the volume is 0.01. Use the measured value as
`--units-per-price` instead of assuming it is 1. The script does not trade.

Then run the tick-export script with `DaysBack=2`. It writes
`FILE_COMMON/Eliot_XAUUSD_l_Ticks.csv` in 15-minute download chunks and
prints the number of ticks and whether the export hit its row cap. A cap
means the requested date range is incomplete; shorten the range or increase
`MaxTicksToExport` for a full coverage check. Export the latest M5 bars again
using `ExportRamonHistory.mq5` and reimport them so timestamps overlap.

```bash
docker compose --profile tools build tools
COMMON="$HOME/.mt5/drive_c/users/$USER/AppData/Roaming/MetaQuotes/Terminal/Common/Files"
test -s "$COMMON/Eliot_XAUUSD_l_Ticks.csv" || exit 1
mkdir -p data/imports
cp -- "$COMMON/Eliot_XAUUSD_l_Ticks.csv" data/imports/
docker compose --profile tools run --rm --no-deps tools -m ramon.eliot_ticks \
  /data/imports/Eliot_XAUUSD_l_Ticks.csv \
  --db /data/eliot_ticks.sqlite3 --bars-db /data/ramon_history.sqlite3 \
  --symbol XAUUSD_l
```

The importer stores Eliot ticks in an **independent** SQLite file, accepts
repeat exports, and checks first quote versus M5 open in at least 20 bars.
An alignment error means the tick and bar clocks or prices do not agree;
investigate it before interpreting any replay. Once aligned, compare the
same Chronos candidates at the next bar open and over three bars. Replace
`1` with the money-check result when needed:

```bash
docker compose --profile tools run --rm --no-deps tools -m ramon.eliot_tick_audit \
  --db /data/ramon_history.sqlite3 --ticks-db /data/eliot_ticks.sqlite3 \
  --symbol XAUUSD_l --model autogluon/chronos-2-small --device cpu \
  --units-per-price 1
```

The tick audit only compares intervals with quote coverage and reports
tick and M5 candle outcomes for identical signals. It does not simulate
latency, commissions, slippage or actual broker execution.

The `--units-per-price 1` assumption comes from the supplied MT5 history:
at 0.01 lot, several closed gold trades show approximately one CENT-account
unit for a 1.0 price move. Confirm this against `OrderCalcProfit` on the
actual symbol before treating the results as executable. The baseline replay
uses only Chronos 2 forecast medians.
It enters at the following M5 open, applies the recorded bar spread, counts
same-bar stop and target as a stop, and closes after at most three bars.
This is an exploratory Eliot probe, not a live trading signal or a net-profit claim.

## Before an M5 live EA

1. Run Chronos 2 on a separately keyed M5 context and forecast horizon.
2. Build and validate role-model features and labels on M5 data only. Do not
   reuse the trained M15 role bundle on M5.
3. Replay chronological M5 periods with recorded spread and
   conservative stop-first ordering for same-bar target/stop touches. Evaluate
   net return, drawdown and win rate; candle data cannot prove tick fill order.
4. Size using `OrderCalcProfit` for the broker's minimum volume. Reject entry
   when a legally placed stop exceeds the USD 0.12 risk cap; do not raise the
   cap just to obtain a five-cent take profit. Verify execution in MT5 tester.
5. Deploy the M5 system independently of the existing M15 EA after validation.

For context, USD 0.05 gains versus USD 0.12 losses need more than 70.6% wins
before costs. A net five-cent target needs to account for spread, commission,
slippage and other fees. The historical M5 spread is only a bar-level proxy.
