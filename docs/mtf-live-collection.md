# Live M1/M5 closed-bar collection (observe only)

This data pipeline **never** trades. It does not change Ramon.mq5 or its
entry gates. Both pieces must be running to refresh the M1/M5 SQLite rows:

1. MT5: Compile `mt5/RamonM1M5Collector.mq5` under **MQL5/Experts** and
   attach **once** to a separate XAUUSD_l chart. It can use any chart period.
   It reads only completed M1/M5 candles by `CopyRates(..., 1, ...)` and
   atomically replaces `Terminal/Common/Files/RamonLive_XAUUSD_l_{M1,M5}.csv`.
   A connection and already-loaded broker chart history are required.
   Its default interval is 60 seconds and exports up to 1500 bars each time.
2. Docker: `docker compose up -d --build mtf-collector dashboard`. The
   importer monitors these files every 30 seconds and upserts into the existing
   `history_bars` table. It preserves the MT5 epoch timestamps, without
   inventing UTC offsets. Only published closed-bar snapshots are imported.

The default Compose mount expects the MT5 Wine user `amir` and prefix
`~/.mt5`; verify that `~/.mt5/drive_c/users/amir/AppData/Roaming/MetaQuotes/Terminal/Common/Files`
contains the CSVs. Adjust `mtf-collector --common-files` if the location
differs. Do not point at `MQL5/Files`, which is different from `FILE_COMMON`.

Checks:

```bash
docker compose ps mtf-collector dashboard
docker compose logs --tail=40 mtf-collector
python3 - <<'PY'
import sqlite3
from datetime import datetime, timezone
c=sqlite3.connect('file:data/ramon_history.sqlite3?mode=ro',uri=True)
for tf in ('M1','M5'):
    n,t=c.execute("select count(*),max(time) from mtf_utc_bars where symbol=? and timeframe=?",('XAUUSD_l',tf)).fetchone()
    print(tf,n,datetime.fromtimestamp(t,timezone.utc).isoformat() if t else None)
PY
```

If the exporter reports CopyRates unavailable, open M1 and M5 charts in
MT5 to load history and wait. If the importer says MISSING, inspect the
FILE_COMMON location. If importer logs an import error, never delete or
reset the existing production history database. The dashboard reports
stale/missing feed data and does **not** silently bypass trade protection.

Run tests after updating the repo:

```bash
uv run pytest -q tests/test_mtf_collector.py
```

The scalping dashboard's M1/M5 freshness uses only `mtf_utc_bars`.
This is a data-quality change, **not** permission to execute scalps. No
trade logic or risk gates have been relaxed. Run tests before enabling.
