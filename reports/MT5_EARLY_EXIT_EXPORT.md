# Read-only MT5 Early Exit data exporter

Script: `scripts/mt5/RamonEarlyExitHistoryExport.mq5`

Purpose: obtain original broker M1 bars and Bid/Ask quote changes for the seven
Early Exit candidates. It does **not** submit, modify, or close orders, change
Ramon EA inputs, or write to Ramon's live SQLite database.

## Running (on HP-Mini)

1. Fetch/merge origin main in your local repository (do not hard reset a dirty branch).
2. Copy the script into the active terminal's `MQL5/Scripts/` directory, open
   in MetaEditor, and compile with F7. Then run it once in MT5 Navigator > Scripts
   on the `XAUUSD_l` chart. Inspect Experts logs for errors/row counts.
3. Exports appear in the terminal's **Common/Files** directory (available via
   MT5 File > Open Data Folder and the common files path). Each research day has
   `Ramon_EarlyExit_XAUUSD_l_YYYYMMDD_M1.csv` with
   `time,open,high,low,close,spread_points` and
   `Ramon_EarlyExit_XAUUSD_l_YYYYMMDD_Ticks.csv` with `time_ms,bid,ask`.
4. **Timestamp verification is mandatory.** The requested windows are interpreted
   by the MT5 API clock (not automatically shifted from UTC). Cross-check M1
   completed-bar close against existing UTC-labelled `XAUUSD_l` M5 close
   (the minute ending each five-minute candle) and check tick `time_ms`
   against a known broker event. Do not assume a server UTC offset merely
   because terminal diagnostics mention one; exported epochs may already be
   canonical UTC.
5. Compare counts and minute coverage; broker may not provide all requested
   historical ticks. Errors, missing chunks, and empty windows are reported.
   Only after verification import M1 **into the existing /tmp test SQLite**
   using `uv run python -m ramon.import_history <M1.csv> --db /tmp/ramon_m1_import_test.sqlite3 --symbol XAUUSD_l --timeframe M1`.
6. Rerun `uv run python -m ramon.early_exit_replay_validator --db /tmp/ramon_m1_import_test.sqlite3 --lookahead-minutes 60`.

Research windows shown in MT5 timestamp convention:
- 2026-10-07 03:30:00 through 10:30:00
- 2026-10-08 09:45:00 through 19:00:00
- 2026-10-13 04:15:00 through 06:00:00

The tick output records valid positive Bid/Ask samples. It does not establish
broker fill, commissions, swap, or broker-side initial SL/TP. An Early Exit
counterfactual must remain NOT_EVALUATED until all required evidence exists.
Tick coverage after 60 minutes should be checked explicitly; no profitable
counterfactual can be inferred merely from the existence of historical quotes.
