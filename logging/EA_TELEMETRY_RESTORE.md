# Restored Ramon research telemetry — pre-deployment checklist

This change is intentionally isolated on `feature/restore-research-telemetry-20261013`.
Do **not** deploy the modified MQ5 to the live cent account before compiling and
testing on a demo / Strategy Tester.

## What it restores
- New sessions: `session_start` and `session_end`
- Once-per-timer sampled open-position quotes (`position_quote`)
- Initial SL/TP/volume and observed changes (`protection_baseline`, `protection_change`)
- Detected observation gaps (`telemetry_gap`)
- Deal execution and closing details, including manual closes on EA-owned positions (`deal`)

The logger writes the existing `RamonResearch_<server>_<account>_<magic>.jsonl`
format, so the existing PostgreSQL `research-log-ingest` service can import it.
It does not re-enable the complete legacy `decision_gate`, `order_intent`,
`close_intent`, or related events yet. Those require explicit per-execution-path
instrumentation and a separate review to preserve reliable actor attribution.

`actor=external_unattributed` means the source of an observed protection change
is **unknown**, not confirmed manual. A blank actor or missing event does not prove
absence of manual intervention.

## Install in a non-live MT5 terminal
1. Copy both `mt5/Ramon.mq5` **and** `mt5/RamonResearchTelemetry.mqh` together
   into `MQL5/Experts/Ramon/` in the test terminal.
2. Open `Ramon.mq5` in MetaEditor, compile (F7), and require **zero errors**.
3. Keep `EnableLiveTrading=false` initially and inspect the Common/Files
   `RamonResearch_*.jsonl` for valid JSONL records and unique `event_id`.
4. On demo only, validate: automatic entry/exit, manual close, SL and TP changes,
   position sampling, restart, gap logging, and a disconnected PostgreSQL service.
5. Run `uv run pytest tests/test_research_telemetry_static.py` and the existing
   EA suite; then separately compare trading behavior with logger on/off.
6. Check PostgreSQL `logging_freshness`, event counts, no duplicate IDs and
   actor/source classification before enabling on the live terminal.

The logger never calls trading functions or HTTP. File writes can still consume
some timer time; evaluate that latency in demo, particularly on slow disks.
No automatic live installation, deployment or EA compilation is performed here.

## Confirm correct file in MetaEditor

This experimental version has `#property version "1.598"` and
`#define RAMON_EA_VERSION "0.59.8"`, and includes
`#include "RamonResearchTelemetry.mqh"` immediately after Trade.mqh.
The old `0.59.7` file can compile successfully **without** telemetry.

Open the exact file from the experimental folder using MetaEditor File > Open.
If the first 8 lines do not show these markers, MetaEditor is compiling the
wrong source path. Keep both the `.mq5` and `.mqh` together. Compile only in the
isolated test folder; don't overwrite the production EA source or EX5.
