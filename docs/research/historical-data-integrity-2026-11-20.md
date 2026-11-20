# Historical data integrity: 2026-11-20

## Progress

| Phase | Requirement | Status | GitHub commit | Test result | Remaining work |
|---|---|---|---|---|---|
| 1 | Read-only inventory | IMPLEMENTED_NOT_DEPLOYED | Pending | PostgreSQL targeted tests passed | Deploy report; monitor ingestion |
| 1 | Trusted 2022–2026 history and reconstruction | BLOCKED | — | No source export available | Obtain original broker exports and timezone rules; compare and stage |
| 1 | Strict replay | BLOCKED | — | Existing replay checks pass | Clean, adequate input and warm-up required |
| 2 | Chronological research and execution realism | NOT_STARTED | — | — | Trusted data and experiment design |
| 3 | Performance intelligence and validation dashboard | NOT_STARTED | — | — | Auditable research runs |
| 4 | Eligibility and degradation | NOT_STARTED | — | — | Approved evidence and rules |
| 5 | ML review and advisory research | NOT_STARTED | — | — | Independent pipeline and leakage audit |
| 6 | Production reliability | NOT_STARTED | — | — | Full operational review |

## Observed inventory

Read-only PostgreSQL query on 2026-11-20 UTC found XAUUSD_o only in 2026, across all sources. For `ramon-command-ea-1`: M1 13,571 rows (Nov 5–20), M5 3,435 (Nov 3–20), M15 3,000 (Oct 26–Nov 20), H1 756 (Oct 6–Nov 20). All M1, M5 and H1 timestamps aligned; 1,243 M15 timestamps were off-grid. Counts are a snapshot, not a continuous feed guarantee. There are also separate broker-identifier and simulation sources; do not combine them without validation. Gap counts in the command include weekends and closures and cannot determine broker-session completeness. The `MarketBar` unique constraint prevents duplicates within a source/symbol/timeframe/timestamp, not overlapping economic candles across sources.

Use the report after deploying the code:

```sh
docker compose exec -T django python manage.py audit_market_bar_quality --symbol XAUUSD_o --first-year 2022 --last-year 2026 > market-quality.json
```

Omit filters for all sources and symbols. Absence of a group means no stored rows. The SHA-256 identifies the ordered stored UTC timestamps and OHLCV values within a group; it is not proof of origin, timezone accuracy, completeness or an immutable dataset version. UTC-aligned timestamps can still be shifted by whole hours. Readiness requires broker-session calendars, coverage and warm-up review in addition to this inventory.

## Provenance and reconstruction

Request immutable MT5 exports for XAUUSD_o M1, M5, M15 and H1 covering 2021 warm-up through 2026, including raw server candle-open times, OHLC, tick volume, spreads where available, broker/server identity, symbol specifications, export timestamp and authoritative historical UTC offset/DST schedule. Preserve originals and SHA-256 checksums outside the production database. Confirm timezone rules around every transition with broker support and independently compare overlapping bars; never apply the current offset to all historic candles. Never round timestamps, interpolate gaps, overwrite conflicts, or merge feeds based on symbol name alone.

Create a versioned, source-identifiable staging dataset with original timestamp, converted UTC timestamp, offset and conversion rule, original file hash and import version. Validate OHLC, closed candles, uniqueness, collisions, grid alignment, exact source-to-staging row reconciliation and broker-session gaps. Compare overlapping legacy rows and isolate any conflicting prices/timestamps for manual review. Rehearse backup and restoration and record the reconciliation results before proposing any production replacement; destructive changes require separate approval. Prefer leaving legacy records intact and importing the verified dataset under a distinct source. An importer and staging schema are not implemented here; there is no authorization to mutate historical records.

## Timestamp evidence and caveats

An EA backup computed `TimeCurrent() - TimeGMT()`; a stalled last-quote clock can introduce varying seconds. Eight offset buckets each contain 128 rows, consistent with repeated 128-candle batches carrying a shared conversion error. This is a hypothesis, not proof: raw broker exports and EA logs are needed to distinguish clock sampling, stale quotes and other processing. The current source normalizes the sampled offset, but applies it to every candle in a batch, so crossing a DST change can produce aligned yet hour-shifted history. Actual compiled EA and deployment, broker DST rules, and incoming live timestamps still require independent verification. Existing ingestion rejects known off-grid timeframes and retains the event, but does not verify whole-hour correctness or completeness.

## Tests and deployment

Targeted PostgreSQL test run: `ea_bridge.tests_data_quality`, `ea_bridge.tests_walk_forward`, and `ramon_pipeline.signalflow.tests.test_replay` passed (19 tests). The report is read-only and adds no database migration. Deploy using the repository's normal image rebuild/release procedure; do not change trading permissions, recreate named volumes or modify broker positions. CI and live EA verification remain outstanding. Do not run the multi-year strict evaluation until source integrity, coverage and warm-up are demonstrated; matching configuration fingerprints do not establish broker execution parity.
