# Trade Forensic Analyzer (read-only)

Open `/api/ea/v1/learning/forensics` or click **Open trade forensics** in
`/api/ea/v1/learning/dashboard`. It uses existing PostgreSQL
`LearningTradeOutcome`, `EACommand`, and `DecisionEvaluation` records.
No database migration, model training, new MT5 command, or strategy-policy
change is required.

The JSON API is `GET /api/ea/v1/learning/forensics/data`; supported query
parameters: `start`, `end` (UTC ISO 8601), `symbol`, `account_login`,
and `limit` (1–500, default 250). The default interval is seven days, with
a maximum 90-day window. Returned results are the **most recent** matching
closed trades, not the entire history when truncated. Use a narrow scope and
check `sample.truncated` before interpreting aggregate statistics.
The HTML date fields include both selected UTC days.

## Interpreting evidence

The outcome's realized broker P/L is the source of net P/L; ensure the
upstream ingestion includes all relevant costs. A trade is
`VERIFIED_EVALUATION` only when its outcome references an exact persisted
OPEN command with matching account, EA instance, symbol and decision, and that
command points to an immutable matching evaluation. `LINKED_COMMAND_ONLY`
means the execution link exists but no verified evaluation does. `UNLINKED`
means attribution cannot be proven: the dashboard **never guesses** based
on closeness in time, matching direction or repeated decision keys.

Regime and setup values may be present as historical outcome tags even
when decision linkage is missing; numeric regime diagnostics (ADX, etc.)
are only shown when sourced from the verified evaluation. Null metrics
mean unavailable, not zero. Evidence only describes observations and cannot
by itself establish whether a loss came from market regime, entry timing,
stop design or broker execution. Use follow-up experiments on chronological
out-of-sample datasets, and compare the same trades between strategies.

No automatic entry restrictions have been introduced. This module has no
write endpoints and does not change MT5 order permissions or pipeline training.
