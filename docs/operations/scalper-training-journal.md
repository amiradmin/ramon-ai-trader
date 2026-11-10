# Scalper logging and training-data audit — 10 November 2026

The database, not container stdout or the MT5 Experts text log, is the durable
research source. The audit initially found 27 forward evaluations, all WAIT,
with no paper or broker scalp outcomes. They already saved 64 M1/32 M5 candles,
quotes, costs and reasons, but lacked receipt timestamps/code artifacts and
persistent entry-gate reasons. There was no outcome-training sample yet.

## Improvements

- New evaluations preserve raw detector results separately from paper-position
  suppression. A WAIT does not erase the original candidate signal.
- Schema v2 captures individual M1/M5 receipt times, quote and heartbeat times,
  policy/configuration, input checksum and exact source fingerprints.
- The append-only ScalpAuditEvent journal archives the exact strategy, observer,
  engine and audit helper source once per code fingerprint/account scope.
  Snapshots reference that artifact. No secrets or .env contents are archived.
- Entry/exit gate reasons are persistent and deduplicated, including pause,
  unavailable data, risk rejection and worker error class. Repeated five-second
  polls do not flood the journal. Engine gate input snapshots are captured in
  the transaction. Container stdout remains a supplemental operational log.
- Claim, acknowledgement and expiry events retain command state/results with
  event timestamps; later acknowledgements cannot overwrite earlier events.
  Broker closed outcomes link the exact opening command and original observation.
- PostgreSQL/SQLite triggers prevent evaluation-input edits and terminal paper
  label rewrites. Audit events reject updates/deletes. Existing historical rows
  are preserved; missing legacy facts are not fabricated.
- Scalp execution metrics now derive decision-to-fill movement from their own
  command quote even when no main Ramon decision exists. Pure request slippage
  remains separate from movement between decision and broker fill.
- Main learning and regression training exclude SCALPER_M1_M5 outcomes. The audit
  found the running background trainer had an older image without these filters;
  it was rebuilt with the current code. No training job was created by this work.

## Export for later analysis

`export_scalper_journal` requires an exact source/account/server/symbol scope and
writes a new local JSONL file without database changes. Existing paths are not
overwritten. Example (replace account identity with the actual demo scope):

```text
python manage.py export_scalper_journal --source ramon-command-ea-1 \
  --account-login YOUR_DEMO_LOGIN --server YOUR_DEMO_SERVER --symbol XAUUSD_o \
  --output /tmp/scalper-journal.jsonl --as-of 2026-11-10T12:00:00+03:30
```

Record types: manifest, evaluation, audit_event, broker_outcome and summary.
Features and future labels are separate. Paper price points/R are never treated
as broker cash P&L. WAIT/NOT_ENTERED, missing outcomes and incomplete windows have
no trade-outcome label and are not losing trades. Broker R stays unavailable when
verified initial cash risk is unavailable; it is not inferred from reserved risk.

The as-of cutoff applies to label settlement and broker receipt times as well as
decision times. A later-settled outcome is unavailable in an earlier export. Legacy
records missing provenance/receipts are labelled with quality issues and excluded
from the v2 paper-training-eligible count. They remain useful for descriptive
analysis. The per-type limit and scope are retained in the manifest; an export
is not an unlimited all-account dump. The output SHA256 is reported.

Training must use chronological splits and purge overlapping label windows across
split boundaries. Never random-split adjacent overlapping candle windows and
claim independent validation. Paper eligibility means adequate recorded evidence
for experimental paper-label analysis, not proof of an edge or readiness to train
and deploy a broker strategy. Keep main/scalp profiles and paper/broker labels
separate. Unknown event/news information must stay unknown.

## Limits

Current inputs are minute OHLC and sampled quotes, not tick history. They cannot
reconstruct exact intraminute ordering, true pre-exit MFE/MAE or every executable
spread. Paper fills use the documented next-full-minute and fixed-cost model;
live scalp commands use current quotes. Replay and paper P&L are not broker P&L.
Health failures are logged while the worker/database are available; a powered-off
host or unavailable database cannot persist an incident at that instant.

## Verification

466 full PostgreSQL regression tests passed in a disposable internal network,
followed by focused audit/cost checks. Tests cover immutable inputs/terminal labels,
source artifacts, receipt/checksum integrity, future-label exclusion, scope
isolation, deduplicated gates, raw-signal preservation and acknowledgement history.
Trading rules, risk settings and broker activation are unchanged. No orders or
training jobs were requested by this audit.

Deployment/export verification: 39 observations were present at the evidence
snapshot, including one schema-v2 observation. The other 38 are explicitly
flagged with missing legacy provenance/receipt/artifact evidence, not rewritten.
The exact scoped export contained 39 evaluations and five audit events, zero
training-eligible paper labels and zero broker scalp outcomes. A local copy is
`research/scalping/journal-audit-2026-11-10.jsonl`, SHA256
`206973be377403f837102914213c5622c9ba23bb704632527ec7d1cea5f1553e`.
Both main-store and regressor isolation were checked in the running training
worker, not inferred from source files alone. The scalp broker gate remains off.
Routine worker stdout now prints on state changes or new observations/settlements,
rather than repeating AUTOMATIC_PAUSED every five seconds.
