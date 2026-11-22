# Import a LiteFinance MT5 M1 export for research

The `import_mt5_export` command reads MT5's tab-separated M1 export for
`XAUUSD_l`. It stores the original minute OHLC and tick volume as `M1` bars
and forms UTC-aligned `M15` bars **only** when every one of the 15 M1 opens
is present. It does not fill gaps, overwrite earlier data, or affect trading
authorization. The isolated source is `litefinance-mt5-live-export-20261122`.

The current export `my_export_XAUUSD_l_M1_202609220101_202611202358.csv`
has SHA-256 `d983965772020ff53c7b6a27e3640334a07152fa16878ba2a4e4f8c59d7c3167`.
The file includes 60,587 valid M1 candles; the provisional UTC conversion
yields 3,958 complete M15 candles and excludes 90 incomplete M15 groups.
There are two short M1 gaps on November 18, at 12:57–12:58 and 13:05–13:06
in broker server time; most other gaps are daily/weekend market closures.
The last UTC M1 open is November 20 at 21:58, so the file contains no
November 21–22 trading data.

The importer assumes the LiteFinance server is UTC+3 in summer and UTC+2
in winter, switching on the last Sundays in March and October. It rejects
data on transition Sundays because the exact switch hour is not established.
This assumption is supported by the terminal log showing UTC+2 on November
22, but it must be checked against independent tick timestamps or matching
EA candles before the derived data is used to validate a trading strategy.

After applying this code on the machine running the stack, copy the two
Python files into the running Django container to avoid restarting the live
service for this one-time import. This container copy is temporary; rebuild
the Django image later to retain the command across container recreation.
The CSV is intentionally not committed. The command requires a filename
containing `_XAUUSD_l_M1_`:

```sh
docker cp backend/django_bridge/ea_bridge/mt5_export.py ramon-django:/app/ea_bridge/mt5_export.py
docker cp backend/django_bridge/ea_bridge/management/commands/import_mt5_export.py ramon-django:/app/ea_bridge/management/commands/import_mt5_export.py
docker cp my_export_XAUUSD_l_M1_202609220101_202611202358.csv ramon-django:/tmp/my_export_XAUUSD_l_M1_202609220101_202611202358.csv
docker compose exec django python manage.py import_mt5_export /tmp/my_export_XAUUSD_l_M1_202609220101_202611202358.csv
```

The command validates the input and prints a dry-run summary by default.
After checking the time mapping and source overlap, run:

```sh
docker compose exec django python manage.py import_mt5_export /tmp/my_export_XAUUSD_l_M1_202609220101_202611202358.csv --apply
docker compose exec django python manage.py audit_market_bar_quality --source litefinance-mt5-live-export-20261122 --symbol XAUUSD_l --first-year 2026 --last-year 2026
```

Research queries must name both source and symbol. The `MarketBar` table has
no spread column; the export's per-minute spread is validated but not stored.
Thus candle OHLC replay does not establish real execution cost or fills.
