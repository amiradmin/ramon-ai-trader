# Ramon V2 — historical MT5 Bid/Ask tick collection (research only)

## Important separation
`mt5/Scripts/RamonExportTicks.mq5` is a **manual MT5 Script**, not an EA, indicator, trading manager, or Docker service. It does not send orders or write to the Ramon SQLite database. You must compile this **new script** in MetaEditor once; there is **no need to recompile Ramon.mq5**. Never attach the exporter to an active automated schedule.

## Procedure on the HP-Mini

1. In MetaEditor open `File → Open Data Folder` via terminal MT5, then copy `mt5/Scripts/RamonExportTicks.mq5` to `MQL5/Scripts/` within the *same MT5 installation*. Compile that file with F7; check the MetaEditor compiler output. The script has not been compiled/verified on the user's terminal by this PR.
2. In MT5 **Navigator → Scripts**, run `RamonExportTicks` manually, selecting the same broker symbol (`XAUUSD_l`), and an initial short historical interval.
3. Select `InpFrom` and `InpTo` carefully; they are **MT5 datetime inputs**, whose relationship to UTC for this broker/terminal must be verified independently against known ticks and server time. Do not blindly treat displayed broker date/time as UTC.
4. Watch the **Experts** log for `complete=true`, `filtered` and errors. The script emits a UTF-8-compatible ANSI CSV with header `time_ms,bid,ask` under MT5's **common files** directory (`Terminal/Common/Files`). A partial export must not be used, including when the tick cap is reached.
5. Copy this file locally to `data/raw_ticks/` (ignored/private recommended). **Do not commit real account data** or large quote archives.
6. Independently establish whether `time_msc` values and decision timestamps use the same UTC epoch-millisecond interpretation. Only after that verification, validate locally:

```bash
uv run python -m ramon.mt5_tick_import \
  --input data/raw_ticks/Ramon_BidAsk_Ticks.csv \
  --output data/research/validated_bid_ask_ticks.csv \
  --report data/research/tick_quality.json \
  --symbol XAUUSD_l --confirm-utc-clock
```

The `--confirm-utc-clock` flag records **operator verification** only; it is not a clock conversion. The importer checks positive, finite bid/ask, monotonic timestamps, exact duplicates and conflicting prices at the same millisecond. Identical-timestamp conflicting quotes are **rejected**, never silently reordered. Report includes largest gaps and observed spread ranges. It does not prove the export is complete or that the symbol matches: the CSV itself lacks symbol provenance, so verify manually.

7. With verified contemporaneous decision-time entry prices, stop, target and horizon:

```bash
uv run python -m ramon.tick_path_dataset \
  --opportunities data/research/decision_entry_plans.csv \
  --quotes data/research/validated_bid_ask_ticks.csv \
  --output data/research/shadow_first_touch_labels.csv
```

Without independently verified plans, **do not reconstruct entry conditions from future candles**. Missing/gapped quote paths remain UNKNOWN.

## Limitations and operational notes
- This is a **batch export**, not live tick capture, auto-upload, automatic Docker synchronization or a continuous broker data recorder.
- `CopyTicksRange` availability, missing broker history, server-clock interpretation, event ordering, terminal constraints and the configured date range affect the quality of output.
- During weekends/market closures, gaps can be legitimate; do not interpolate.
- Always trial a short sample first. Large exports can consume RAM/disk on the HP-Mini. The script partitions requests by time but still loads one chunk into an array.
- The script can write an incomplete CSV if copying aborts; discard it unless the log explicitly says `complete=true`. Nonzero filtered quotes require further review.
- If there are same-millisecond quote changes, the strict importer rejects those rows; obtain a sufficiently ordered archive or mark those intervals unusable rather than inventing their ordering.
- No live deployment of TP/SL probability models is authorized by this change.

## Automated tests
```bash
uv run pytest -q tests/test_mt5_tick_import.py tests/test_tick_path_dataset.py tests/test_tp_sl_shadow.py
```
