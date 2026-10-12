# Ramon time reconciliation (UTC)

Canonical storage uses Unix UTC seconds. The display timezone is configurable and **must not** be used when joining bars or trades.

## Verified on the 2026-10-12 audit

- Legacy `history_bars` M1 matches `mtf_utc_bars` exactly after adding 10,800 seconds to canonical UTC: 1,499/1,499 matching OHLC rows.
- Legacy M5 likewise matches 1,500/1,500 rows after the same +10,800-second adjustment.
- Aligning unadjusted epochs incorrectly pairs unrelated bars. Do not train on a join between legacy M1/M5 and canonical M1/M5 by timestamp alone.
- The live collector already persists explicit `broker_time` and `offset_seconds`.

## Install the safe canonical view

After pulling the commit on the trading host:

```bash
cd ~/Documents/Presentation/ramon-ai-trader
git pull --ff-only origin main
uv run python -m ramon.utc_history_audit --db data/ramon_history.sqlite3
uv run python -m ramon.utc_history_audit --db data/ramon_history.sqlite3 --apply
```

If `ramon` is not installed into the local environment:

```bash
PYTHONPATH=src python3 -m ramon.utc_history_audit --db data/ramon_history.sqlite3 --apply
```

The `--apply` flag only creates the `canonical_utc_bars` VIEW; it does not rewrite `history_bars`, trading decisions, order records or learning labels.

```sql
SELECT timeframe,time,datetime(time,'unixepoch') AS utc_time,
       source,time_quality,broker_time,offset_seconds
FROM canonical_utc_bars
ORDER BY time DESC LIMIT 20;
```

## Mandatory safeguards before a full migration

1. M1/M5: always prefer verified canonical `mtf_utc_bars`; treat legacy `history_bars` M1/M5 epochs as broker-clock until individually validated.
2. M15: `history_bars` may have multiple sources (EA/live and historical CSV), so its current `time` is intentionally marked `UNVERIFIED_LEGACY_EPOCH` in the view. **Do not shift the entire M15 table** by three hours.
3. Deals, decisions, snapshots: record both raw broker time and explicit UTC offset before migrating; validate joins by `sample_key` / trade ticket and price. Do not shift all records heuristically.
4. DST / broker-server timezone changes must use effective-time-specific offsets, not a permanent universal +3.
5. Prove all dependent backtests, reports, online signal processing, and training queries use canonical UTC before removing legacy paths. The VIEW alone does not automatically redirect existing queries.

## Validation status

The code introduces the canonical read layer and a regression test. A trading-host end-to-end validation and downstream consumer migration remain required. Until completed, this is **not** a claim that all historical timestamps have been permanently normalized.

## 2026-10-12 M15 corroboration

The uploaded 07:58 UTC SQLite snapshot yielded **503/503** exact high/low matches for complete M5 triples when comparing M15 broker-clock epochs to M5 canonical UTC at offset +10,800 seconds. Zero complete triple matches were found at offsets zero and −10,800 seconds. This establishes the conversion for these verified records only, not all historical M15 rows.

### Automatic entry audit at snapshot time

On October 12 the signal CSV contained eight final BUY snapshots: six at 06:56:08–06:56:58 UTC (AI V2), and two at 07:06:01 and 07:34:46 UTC (RANGE). No matching AUTO_RAMON entry was recorded in trade_outcomes for that day's four HUMAN_ASSISTED entries. Six AI V2 snapshots reported minimum-lot estimated risk 0.1101 USD against base budget 0.06 USD and executable cap 0.15 USD, with override flag enabled. These are snapshots, not eight independent trade opportunities. Only terminal Experts/Journal order-send logs can establish whether an eligible attempt was blocked or rejected at the EA/broker stage. Do not claim a missed executable order before correlating these events.
