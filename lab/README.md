# Ramon Market Research Laboratory

This laboratory is **not a live strategy**. It reads already-imported OHLC candles from the running Timescale/PostgreSQL server and compares TREND_PULLBACK with an experimental IMPULSE_CONTINUATION replay. It does not download data, train on live ticks, create EA orders, run migrations, change balances or change existing Docker workers.

The original PR #211 was closed without merging. Its research module has been moved under `lab/` in this new PR. Existing `main` trading code remains unchanged.

## Security and isolation

- Docker Compose profile `lab`: not started by ordinary `docker compose up`
- A **separate PostgreSQL role** `ramon_lab_reader` with SELECT on `public.ea_bridge_marketbar` only
- No MT5 token, no EA endpoint, no published ports, no trading workers
- Command creation/execution and all strategy switches are hard-disabled
- Explicit READ ONLY database transaction, read-only container filesystem, no added capabilities
- Does not mount host code or live data volumes; reuses existing internal Compose network only to reach PostgreSQL
- Requires existing `ramon-django:latest` image to be built by the live stack; building the laboratory does **not** rebuild or restart live services

## First identify previously imported datasets

From your existing **live main checkout** (not the research worktree), you can inspect data **without** installing the lab:

```bash
docker compose exec -T django python manage.py shell -c "
from django.db.models import Count, Min, Max
from ea_bridge.models import MarketBar
for r in MarketBar.objects.values('source','symbol','timeframe').annotate(
    candles=Count('id'), first=Min('timestamp'), last=Max('timestamp')
).order_by('source','symbol','timeframe'):
    print(r)
"
```

The exact `source` key matters. Data imported from files may use a different source from live `ramon-command-ea-1`. Do not merge different broker symbols / sources or timeframes. Training data and live replay data need separate lineage.

## Install the lab into a separate checkout

Do not replace the running real-trading checkout. After PR is reviewed, fetch and check out the lab branch in an **independent** Git worktree:

```bash
cd ~/Documents/Presentation/ramon-ai-trader-bridge
git fetch origin
git worktree add ../ramon-market-lab origin/feature/isolated-market-research-lab-20261123
cd ../ramon-market-lab
# Compose references .env for existing services. This symlink stays
# outside Git (the repository ignores .env) and is NEVER added to the lab image.
ln -s ../ramon-ai-trader-bridge/.env .env
```

**Do not run** `docker compose up` with only the main compose file from the laboratory worktree.

First provision the least-privilege role through the **existing** PostgreSQL container while the live system stays up. From the main checkout, choose a unique lab password and use psql interactively; do not paste credentials into a chat or commit a `.env` file. With the live database administrator role, execute `lab/create_reader.sql` using a secure variable mechanism, then verify `SELECT` works and `INSERT` is denied. You can also have your database administrator provision the role manually with exactly the privileges in that file.

Supply the lab role password to Compose through the `LAB_DB_PASSWORD` environment variable (shell secret management). Other Compose database variables come from the **existing** private environment file; Compose only passes its explicitly listed variables to the lab container, not the full `.env`.

```bash
cd ~/Documents/Presentation/ramon-market-lab
# Set LAB_DB_PASSWORD securely in your environment, without committing it.
docker compose --env-file ../ramon-ai-trader-bridge/.env \
  -f docker-compose.yaml -f docker-compose.lab.yaml \
  --profile lab build laboratory

docker compose --env-file ../ramon-ai-trader-bridge/.env \
  -f docker-compose.yaml -f docker-compose.lab.yaml \
  --profile lab run --rm --no-deps laboratory --list-data
```

If `--list-data` reports only historic `XAUUSD_o`, choose the correct imported source/symbol for the research period instead of labeling it `XAUUSD_l`. The listing reports candle counts, first and last timestamp, and actual source labels.

## Example M15 comparison (illustrative cost inputs)

Only run after the lab catalog confirms that source, symbol and dates exist:

```bash
docker compose --env-file ../ramon-ai-trader-bridge/.env \
  -f docker-compose.yaml -f docker-compose.lab.yaml \
  --profile lab run --rm --no-deps laboratory \
  --source litefinance-mt5-live-export-20261122 --symbol XAUUSD_l \
  --start 2024-01-24 --end 2026-11-20 \
  --balance 1024.23 --spread-points 42 \
  --point 0.01 --tick-size 0.01 --tick-value 1 \
  --volume-min 0.01 --volume-max 100 --volume-step 0.01 \
  --slippage-points 2 --commission-per-lot 0 \
  > impulse-lab-comparison.json
```

The `42` spread points and `2` slippage points are example assumptions, **not historical measurements**. Verify cent-account tick currency and use spread/slippage stress testing. If the source has 47 gaps as previously observed, the results are only descriptive, not suitable for live strategy selection.

## Tests

Run the lab source syntax check from the laboratory checkout:

```bash
python3 -m py_compile lab/run.py lab/impulse_continuation_research.py
docker compose --env-file ../ramon-ai-trader-bridge/.env \
  -f docker-compose.yaml -f docker-compose.lab.yaml config --quiet
```

A passing code compile does **not** validate trading accuracy. No direct MT5 commands can be created by this lab.

## Full historical experiment and offline dashboard

Your current database inventory shows **65,424 imported XAUUSD_l M15 candles**
in source `litefinance-mt5-live-export-20261122`, plus 1,000,000 M1
candles. The imported series is the **research data source**. Do not
combine it with live `ramon-command-ea-1` bars or the different `XAUUSD_o`
symbol.

The long replay reads M15 using a named PostgreSQL cursor and `--batch-size
2000` (configurable 100–10,000). It evaluates both strategy models **once**
through the complete ordered sequence, so a position does not restart at a
database batch boundary. Each strategy is simulated independently.

The report applies *predeclared*, non-overlapping dates by **entry time**:

| Group | Dates (UTC) |
| --- | --- |
| TRAIN | 2024-01-24 to 2025-06-30 |
| VALIDATION | 2025-07-01 to 2026-03-31 |
| TEST_UNTOUCHED | 2026-04-01 to 2026-11-20 |

This is a **chronological evaluation split, not an automated model
training/tuning pipeline**. No parameters are optimized and no claim of
out-of-sample edge may be made without a separately documented selection
process. Boundary-crossing positions are flagged; trades are attributed to
the entry period. Open/unclosed trades at the end are not counted as wins.

Run the following in the standalone laboratory checkout after the reader role
has been created. Keep the existing trading containers running normally.

```bash
cd ~/Documents/Presentation/ramon-market-lab
docker compose -f docker-compose.yaml -f docker-compose.lab.yaml \
  --profile lab run --rm --no-deps laboratory \
  --source litefinance-mt5-live-export-20261122 \
  --symbol XAUUSD_l --start 2024-01-24 --end 2026-11-20 \
  --balance 1024.23 --spread-points 42 \
  --point 0.01 --tick-size 0.01 --tick-value 1 \
  --volume-min 0.01 --volume-max 100 --volume-step 0.01 \
  --slippage-points 2 --commission-per-lot 0 \
  --batch-size 2000 > impulse-lab-comparison.json

python3 lab/render_report.py impulse-lab-comparison.json > impulse-lab-dashboard.html
xdg-open impulse-lab-dashboard.html
```

**Important:** `LAB_DB_PASSWORD` must be exported in the calling shell
before invoking Docker Compose and must match the dedicated reader role.
`POSTGRES_PASSWORD` is loaded from the private `.env` symlink. If the
original `.env` is not present, stop and configure it; never commit it.

The HTML dashboard opens locally, needs no running web server and
contains no remote JS/CDN. JSON and HTML are generated on the host using
stdout redirection; the laboratory container filesystem remains read-only.
The initial quoted spread (42 points) was one observed live quote, not
the historical bid/ask spread distribution. Re-run with several worse cost
scenarios and verify cent-unit contract economics.

The laboratory reports history fingerprint (SHA256), excluded/off-grid
candles, gaps, full run summaries, and training/validation/test slices.
Both the existing TREND_PULLBACK pipeline and the experimental candidate are
descriptive OHLC simulations, not live broker execution models. No result
is eligible to automate real trading directly.

## Diagnostics and transaction-cost stress (2026-11-23)

The full historical run on 65,424 M15 candles had **300 experimental
IMPULSE_CONTINUATION signals, all rejected by MINIMUM_LOT_EXCEEDS_RISK**,
and 742 interval discontinuities. These are observations from the previous
local report, not a prediction of profits. The laboratory now includes:

1. `candidate_risk_diagnostics`: a detailed entry-by-entry record of each
   minimum-lot rejection, including signal timestamp, trade side, stop
   distance (price and points), cash risk at the smallest lot, cash risk
   as percent of running equity, available 0.5% risk budget, approximate
   affordable volume, and required account balance at 0.5% risk.
   The laboratory does **not** bypass the 0.5% ceiling.
2. `contract_diagnostics`: mathematical checks for submitted parameters
   with a **permanent unverified** badge. Confirm the actual account currency,
   cent-denominated balance units, tick-value denomination, contract size and
   broker volume constraints manually. The tool does NOT infer real broker
   values from OHLC data.
3. `gap_diagnostics`: UTC-based heuristics classifying likely weekend gaps,
   multiday closures, other long closures, irregular timestamps and
   unexplained intraday gaps. This is NOT an authoritative broker session
   calendar. Strategy simulation still treats missing OHLC price paths as
   unobserved.
4. `annual_performance` and `regime_performance`: observed closed-trade
   metrics by year and pre-existing signal regime labels. Unlabeled
   trades remain UNCLASSIFIED.
5. `cost_sensitivity`: optional independent replays at 1x, 2x and 3x
   the supplied spread and slippage; risk settings and input history remain
   identical. This re-runs the full TREND_PULLBACK pipeline and may be
   substantially slower than the base comparison.

Append **`--cost-stress`** to the previous laboratory `docker compose run`
command to include the three cost scenarios in its JSON. For example:

```bash
# From ~/Documents/Presentation/ramon-market-lab
docker compose -f docker-compose.yaml -f docker-compose.lab.yaml \
  --profile lab run --rm --no-deps laboratory \
  --source litefinance-mt5-live-export-20261122 \
  --symbol XAUUSD_l --start 2024-01-24 --end 2026-11-20 \
  --balance 1024.23 --spread-points 42 \
  --point 0.01 --tick-size 0.01 --tick-value 1 \
  --volume-min 0.01 --volume-max 100 --volume-step 0.01 \
  --slippage-points 2 --commission-per-lot 0 \
  --batch-size 2000 --cost-stress > impulse-lab-diagnostics.json

python3 lab/render_report.py impulse-lab-diagnostics.json \
  > impulse-lab-diagnostics.html
xdg-open impulse-lab-diagnostics.html
```

The report is **descriptive only** and cannot establish broker-correct
profitability. Never relax the real risk ceiling or move these candidate
rules into real execution based solely on simulated gains. Preserve any
error from stderr; a failed run may leave a zero-byte redirected JSON file.

## Contract verification and cost-rejection/gap drilldown

The existing 1x cost scenario generated 49 TREND_PULLBACK trades, but 2x/3x
produced **zero**. That is NOT a breakeven P&L backtest: we must inspect
pipeline rejection reasons per scenario before claiming cost robustness.
The new JSON includes each scenario's
`cost_sensitivity[].trend_pullback.rejections`, `rejection_total`,
and `evaluations`. The offline HTML dashboard shows rejection stage,
reason, and count. The `gap_diagnostics` section now includes the
highest-frequency UTC weekday/hour combinations for unexplained gaps,
the year breakdown and up to 30 sample timestamps. These are clues,
NOT proof of missing candles or valid sessions.

**Read-only MT5 snapshot:** On the same computer and Python environment
that already has a *connected* MetaTrader 5 terminal and working
`MetaTrader5` Python module, run:

```bash
python lab/inspect_mt5_contract.py --symbol XAUUSD_l > mt5-contract-snapshot.json
```

On Ubuntu/Wine, ordinary Linux `python` usually cannot import the Windows
MT5 Python integration. The script must be run with the compatible Python
interpreter associated with an already connected terminal; it never opens
orders or changes account settings. The snapshot excludes login/account ID,
server identity and authentication details. It DOES contain current balance,
which is sensitive financial data: **do not commit either snapshot or report
to Git**.

When the next lab backtest has produced `impulse-lab-diagnostics.json`,
compare the snapshot *offline on the host* (no extra DB permissions):

```bash
python3 lab/verify_mt5_contract.py \
  mt5-contract-snapshot.json impulse-lab-diagnostics.json \
  > contract-verification.json
python3 -m json.tool contract-verification.json
```

The comparison reports matched and mismatched contract fields, including
tick size/value and minimum volume. It never updates the replay assumptions,
cannot authorize real trading, and flags both cent-account denomination
and historical contract changes as still unverified. The MT5 balance may
have changed since the historical starting balance; that difference
is reported but should not be automatically corrected.

After rebuilding only the laboratory container, rerun the standard
`--cost-stress` command to obtain new rejection counts and richer gap details.
The isolated live MT5 worker does not need restarting.

## Ubuntu/Wine MT5: export the broker contract without Python MetaTrader5

The pure Linux `python lab/inspect_mt5_contract.py` may say
`MetaTrader5 Python API missing`. Do **not** install a new MT5 instance
or change the live EA.

Use the new read-only **`lab/RamonContractSnapshot.mq5`** instead:

1. In your already connected MetaTrader 5 desktop application, open
   **File → Open Data Folder**. Open `MQL5/Scripts` and copy
   `lab/RamonContractSnapshot.mq5` from the laboratory Git checkout into
   that Scripts folder. You can also open the script from the terminal's
   MetaEditor, save it under `MQL5/Scripts`, and compile there.
2. Compile the file using MetaEditor (F7). This script only reads
   `AccountInfo*`, `SymbolInfo*` and writes one JSON text file.
   It calls **no** `OrderSend`, trading classes, position modification
   or HTTP functions.
3. In MT5 **Navigator → Scripts**, run `RamonContractSnapshot` with
   **ResearchSymbol = XAUUSD_l**.
4. MT5's **Experts** tab will print the output location. Open
   **File → Open Data Folder → MQL5 → Files** and copy
   `ramon-contract-snapshot.json` into the lab checkout as
   `mt5-contract-snapshot.json`. It includes current account balance,
   so keep it private.
5. Run:

```bash
cd ~/Documents/Presentation/ramon-market-lab
python3 -m json.tool mt5-contract-snapshot.json >/dev/null &&
python3 lab/verify_mt5_contract.py \
  mt5-contract-snapshot.json impulse-lab-diagnostics.json \
  > contract-verification.json &&
python3 -m json.tool contract-verification.json
```

**Note:** MetaEditor compilation and execution must happen on your actual
connected MT5 installation; GitHub CI does not compile MQL5, so inspect the
MetaEditor errors if any. This snapshot represents current broker specs,
not necessarily the full 2024–2026 historical contract.
