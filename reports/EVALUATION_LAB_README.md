# Ramon Evaluation Lab — phase 1: audit before inference

This is a READ-ONLY preliminary assessment of `ramon_history.sqlite3`.
It DOES NOT train a model, submit trades, or imply the strategy is profitable.

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run --with pytest python -m pytest -q tests/test_evaluation_lab.py
uv run python -m ramon.evaluation_lab --db data/ramon_history.sqlite3
```

Dashboard: `http://127.0.0.1:8013/monitor` (Evaluation Lab card).
JSON: `http://127.0.0.1:8013/api/evaluation-lab`.

Auto outcome candidates must have known entry source `AUTO_RAMON` or `RANGE_AUTO`,
a closed outcome, finite net units and net R, a matching recorded decision and no
**recorded** manual intervention. Trades missing an intervention column fail closed.
The values are PROVISIONAL because missing intervention telemetry cannot prove
absence of human intervention. This is not a basis for switching to a dollar account.

Decisions are counted within the latest bounded SQLite sample; repeated
same-signal samples may not be statistically independent. Profit Factor and
win-rate are descriptive, not holdout/walk-forward guarantees. Cent-account
`net_units` are in account units, not USD.

WAIT Replay: explicitly `NOT_EVALUATED`. Before enabling it, audit M1/tick data
timestamps and bid/ask, tick-size, spread assumptions, missing candles, fills,
conservative both-hit SL/TP handling, and ordered independent-day walk-forward
holdouts. Do not derive successful untraded outcomes from future prices with
an unverified fill mechanism.

The source audit currently reads SQLite; PostgreSQL event-to-order matching
and detailed human provenance audits will be separate next-phase additions.

## Loss Attribution (descriptive, provisional)

The report includes `loss_attribution` grouped by exit_detail, exit_reason,
entry_source, direction, entry UTC hour and EA version. Categories marked
`UNKNOWN` reflect missing source fields, not inferred events. Each category
returns trade count, wins, losses, account-unit net P&L, mean R and profit factor.
Missing initial-risk observations are reported as coverage counts. Regime at
entry and initial broker SL/TP are intentionally `NOT_JOINED`/`NOT_EVALUATED`
until independently verified. Comparisons are observational, not causal, and
manual-action completeness remains unverified. Do not optimize trade rules from
small groups or declare dollar-account readiness on these outputs.
