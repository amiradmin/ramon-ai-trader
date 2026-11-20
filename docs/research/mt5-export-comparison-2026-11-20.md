# MT5 M15 export investigation

The local `new_XAUUSD_oM15.csv` under the installed terminal directory contains 100,068 rows, 2022-08-15 through 2026-11-10. Its SHA-256 and row integrity inventory are in `mt5-m15-export-quality-2026-11-20.json`; the export's exact provenance, broker timezone and historical DST rules remain unverified. It has no warm-up before August 2022, and an aligned timestamp does not prove it is UTC. No corresponding multi-year M1, M5 or H1 CSV has yet been located.

A read-only exact OHLC comparison against `ramon-command-ea-1` M15 database rows from 2026-10-26 through 2026-11-04 found 1,966 unique export matches for 1,966 stored rows. Database timestamp minus the naive export timestamp, treated as UTC purely for arithmetic, was -7200 seconds for 723 rows, -7199 for 219, and -7198, -7192, -7176, -7161, -7146, -7131, -7116 and -7101 for 128 each. This matches the observed off-grid patterns and strongly supports shared batch clock-offset errors. It does not establish a year-round UTC conversion or prove the export itself is authoritative. Verify broker offset and DST independently before any import. Match collisions, invalid data and historical changes must be reviewed; do not round or update existing records.

Reproduce export inventory without DB writes:

```sh
python3 scripts/research/audit_mt5_export.py '/home/amir/.mt5/drive_c/Program Files/MetaTrader 5/new_XAUUSD_oM15.csv' --minutes 15
```

The 1,100 elapsed gap intervals include closures and are not verified missing trading candles. Obtain broker session calendars and the M1/M5/H1 exports before claiming coverage or running the multi-timeframe replay. Store raw exports and checksums, and stage a source-identifiable dataset with raw broker timestamps and explicit per-period offset provenance; no import has been performed.
