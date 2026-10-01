# Research telemetry v1 — EA 0.53.6

This records observations only. Entry thresholds, role influence, position sizing,
SL/TP and exit conditions retain their existing policy. Every robot close uses a
logging wrapper that calls the same CTrade operation and returns the same result.
Telemetry failures never veto an entry/exit. Local file I/O and idle HTTP uploads
do add overhead; measure this on the target HP Mini before interpreting latency.

## Install on the existing HP Mini

```bash
git pull --ff-only origin research/chronos-covariates-entry-validation
bash scripts/install_research_telemetry.sh
curl --fail http://127.0.0.1:8012/health
```

The installer makes a SQLite backup including committed WAL data, compiles with
the existing MetaEditor, then restarts only the model using the current source
mounted read-only. It does not rebuild the image or touch the forward worker.
Reload the EA on both charts, retain MAIN/SMALL magic and chart settings, and
verify the displayed version is 0.53.6. Research inputs default to enabled and
1000 ms sampled quotes. Sampling is clamped to >=1000 ms to limit disk activity.
Use 5000 ms if storage latency is excessive; the interval is recorded in data.
No MetaEditor is available in the development environment: the install step is
the required target compile check. Existing source-contract/Python tests do not
substitute for this compile or broker execution validation.

When recreating the model later without an image rebuild, continue including
`-f compose.yaml -f compose.telemetry.yaml`; otherwise Compose will revert to
the source baked into the old image. Build a new image when the mirror is fixed
to remove that operational dependency. Older EAs remain protocol-compatible,
but do not produce the new event stream.

## Data contract

| Storage | Contents |
| --- | --- |
| `input_blobs` | Compressed, content-addressed completed bars and observed news event lists; repeated context is shared. |
| `inference_audit` | Exact quote/microbar/request envelope, full response, model/revision, role manifest hashes, feature snapshots, settings, source digest, service run ID, news availability time, cache reuse, median/10%/90% forecast paths and decision duration. |
| `research_events` | Idempotent EA events: session, final gate/status with config and observed values, entry/close requests/results, broker deals with order/position IDs, fees and millisecond clock, exit detail, sampled quotes, SL/TP/stage/volume changes, network errors and observed gaps. |
| `trade_outcomes` | Fully closed position net costs and initial risk; all manual and automatic outcomes retained. New `manual_intervention` covers partial manual closes even when the last fill is TP/SL. |
| `opportunity_labels` | Explicit price-only fixed-horizon labels for all audited opportunities, including WAIT/vetoed decisions. Never interpreted as realized or simulated replacement P&L. |

Decision identity is `sample_key`, not `(captured_second,symbol)`. Migration
preserves existing IDs/columns/indexes and removes the old uniqueness that could
drop same-second MAIN/SMALL requests. Legacy identity-free callers retain their
old deduplication. Old inputs cannot be reconstructed exactly and remain missing.

Broker and UTC clocks remain distinct. Quote/deal `time_msc` is broker evidence;
`observed_utc`/`received_utc` is an observation, not a relabeling of broker time.
The observed offset/config/source fingerprint is recorded separately. EA source
SHA256 excludes its own fingerprint declaration; Python digest covers module
sources. Role manifests retain artifact hashes; unavailable revisions stay null.

### Manual close policy

Desktop (`DEAL_REASON_CLIENT`), mobile, web and dashboard manual closes are stored
with all financial results. Ownership follows the **opening position's magic**,
so a closing fill with magic 0 is included in raw events, legacy CSV and deal
provenance. `sample_key` is recovered from the opening deal when the exit comment
does not contain it. Dashboard reason is stored in `deal_detail` and existing
deal telemetry. Partial closes are preserved as individual deals; aggregate
outcome waits for full closure and includes all costs.

Manual interventions are `CENSORED_MANUAL` for autonomous-policy training;
they remain in account-performance analysis and the exported dataset. Legacy
dashboard rows previously marked LEARNABLE are backfilled to this status. This
does not enable a manual-exit training policy or modify live trade decisions.
Trades without original sample/risk attribution cannot become clean labels;
their raw events remain available, and quality reporting exposes missing joins.

## Reliability and limits

Events are appended/flushed to `FILE_COMMON/RamonResearch_<server>_<login>_<magic>.jsonl`
before upload. A separate byte cursor advances only after the server atomically
accepts a batch. Retrying a batch cannot create duplicate events; reusing an ID
with different content is rejected. Session IDs include clock, process monotonic
counter and chart ID. MAIN/SMALL outboxes and cursors are separate.

Uploads run on alternating idle timer cycles with closed-outcome synchronization;
no network runs in OnTick, and exits/model requests retain scheduling priority.
Do not remove/modify an active outbox/cursor. Keep backups of FILE_COMMON and
`data/ramon_history.sqlite3`. A crash/disk failure can still truncate the last
record or interrupt cursor writes; a rejected batch remains queued for inspection.
Watch the Experts log and `/health.telemetry_last_error`; no delivery guarantee
can be made while the terminal, disk or service is unavailable. The existing
closed-position recovery scans 30 days of broker history; the new event outbox
survives restarts but cannot capture terminal-offline ticks or historical intent.

The exported `observed_path` uses BUY Bid / SELL Ask for sampled MFE/MAE and
first-observed target levels. It is **not tick-exact first-touch ordering** or a
complete execution replay; crossings/extrema between observations may be missed.
OHLC-derived target outcomes retain their separate source. News event lists are
the version observed at that time, not a later revised calendar downloaded for
backtesting. All received decisions are recorded; same-bar samples and overlapping
four-bar outcomes remain correlated, not independent trades.

## Quality report and export

```bash
bash scripts/research_telemetry.sh report
bash scripts/research_telemetry.sh label-opportunities
bash scripts/research_telemetry.sh export --out /data/research/training-audit.jsonl
```

Report counts manual exits, missing immutable inputs, missing decision joins,
missing costs/fills/quotes/gates and groups outcomes by role, EA version and
training status. Export holds one SQLite read snapshot and includes exact
inputs, news, audit, events, manual labels and price-only opportunity labels.
Outcomes without decision samples and unlinked events are exported separately;
they are not quietly dropped. Dataset manifest includes SHA256, sample count,
creation time and the chronological/purged split policy. Export does not train,
choose a threshold, or promote a model.

Before training after 500 total trades, inspect **clean counts per role/policy**,
both classes, provenance coverage and temporal coverage. Reserve untouched later
data for evaluation; fit scalers/calibration on prior data only and purge
overlapping result intervals. There is no automatic assertion that 500 total
trades are sufficient or that forecast accuracy means trading profitability.
