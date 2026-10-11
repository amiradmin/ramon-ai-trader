# Trade Memory — offline shadow diagnostic

Ramon now has a **read-only** point-in-time snapshot tool. It does not connect to MT5 or change the live model, EA, dashboard, orders, risk thresholds or promotion system.

From repository root after installing the project dependencies:

```bash
uv run python -m ramon.trade_memory \
  --db data/ramon_history.sqlite3 \
  --as-of 2026-10-10T23:59:59Z \
  --minimum-samples 30 \
  --output data/trade_memory_snapshot.json

uv run pytest -q tests/test_trade_memory.py tests/test_trade_audit.py
```

If using the existing tools container (with code already rebuilt into its image):

```bash
docker compose --profile tools run --rm --no-deps tools \
  -m ramon.trade_memory --db /data/ramon_history.sqlite3 \
  --as-of 2026-10-10T23:59:59Z
```

**Meaning:** a group flagged `eligible_for_review` meets only a minimum sample-count threshold. It is *not* authorized for live trading, statistically validated, or necessarily profitable. Source tables: `trade_outcomes` joined to `decision_samples` on exact `sample_key`. Only outcomes closed by the as-of timestamp and captured before trade opening enter the snapshot. Unmatched outcomes are excluded, not imputed. Reported profitability in this tool is in **net R**, not dollars, cents, or account currency. Hour groups are UTC entry hours; regime attribution is intentionally omitted because entry-time regime provenance needs validation.

Known gaps: join coverage auditing; data drift; genuine London/New York DST sessions; costs and fill telemetry validation; holdout and purged walk-forward; uncertainty bounds. These should precede any use in position decisions.

**Safety:** Do not place trades based on this report. Do not change live risk settings while integrating the memory module.
