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
  --source ramon-command-ea-1 --symbol XAUUSD_l \
  --start 2026-09-22 --end 2026-11-22 \
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
