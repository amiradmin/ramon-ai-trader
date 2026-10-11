# Execution provenance audit for WAIT/conflicting trade directions

This is an **offline, read-only** investigation tool. Its purpose is
to find what evidence exists for trades whose original stored
`decision_samples.direction` conflicts with the executed trade and
whose `final_decision` is `WAIT` or also fails to match.

Observed in the previous direction audit (440 closed trades):
- 316 original `direction` matches
- 50 `final_decision` matches the trade
- 68 `final_decision=WAIT` conflicts
- 2 neither stored direction matches
- 4 missing exact decision links

The tool performs exact `symbol+sample_key` linkage and reads these
**persisted** columns (where available):
- trade `entry_source`, `trade_role`, `entry_magic`, `entry_ea_version`
- decision `direction`, `base_decision`, `final_decision`
- decision capture and trade open timestamps
- optional count of `research_events` with the same `sample_key` and symbol

`entry_source` or magic is *not* proof the EA legally overrode WAIT:
the saved database currently has no guaranteed order-request/EA-policy
authorization chain for this audit. Research event counts are only a
diagnostic; they do not establish order causality or MT5 dealer acceptance.
Therefore `FINAL_FIELD_WAIT` and `NO_RECORDED_DIRECTION_MATCH` remain
`UNRESOLVED_EXECUTION_WITH_CONFLICT` regardless of entry_source.

Run after pulling a CI-approved version:

```bash
cd ~/Documents/Presentation/ramon-ai-trader
uv run --no-sync python -m ramon.trade_execution_provenance_audit \
  --db data/ramon_history.sqlite3 \
  --symbol XAUUSD_l \
  --broker-utc-offset-seconds 10800 \
  --output data/research/trade_execution_provenance.csv \
  --report data/research/trade_execution_provenance_quality.json

cat data/research/trade_execution_provenance_quality.json
```

Report includes classifications and groups of conflicting records
by source, role and magic. The detailed CSV includes one line per trade.

Next step if evidence remains unresolved: obtain dated EA decision, gate,
order request and broker deal logs with shared stable correlation identifiers;
do not infer a permission just to increase sample counts.

No orders, broker communication, SQLite writes, training-label changes,
EA source edits, or risk changes are made. Clock offset must be independently
verified for the historical interval under review.
