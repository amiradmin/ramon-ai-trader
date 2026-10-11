# Trade direction provenance audit (read-only)

This audit investigates cases where `trade_outcomes.direction` differs
from the persisted `decision_samples.direction`. It compares the **same
sample_key and symbol** to `base_decision` and `final_decision`. It does
not interpret the fields as definitive evidence of a valid EA reversal and
does **not** loosen the executed-trade dataset eligibility.

Run on the HP Mini after GitHub CI passes and the change lands on main:

```bash
cd ~/Documents/Presentation/ramon-ai-trader
uv run --no-sync python -m ramon.trade_direction_provenance_audit \
  --db data/ramon_history.sqlite3 \
  --symbol XAUUSD_l \
  --broker-utc-offset-seconds 10800 \
  --output data/research/trade_direction_provenance.csv \
  --report data/research/trade_direction_provenance_quality.json

cat data/research/trade_direction_provenance_quality.json
```

Recorded directions are described as `RECORDED_DIRECTION_MATCH`,
`FINAL_FIELD_MATCHES_TRADE`, `BASE_FIELD_MATCHES_TRADE_ONLY`,
`FINAL_FIELD_MISSING`, `FINAL_FIELD_WAIT`, `FINAL_FIELD_OTHER`,
`NO_RECORDED_DIRECTION_MATCH`, or time/identity exclusions.
`FINAL_FIELD_MATCHES_TRADE` means **only** that the persisted field
agrees with the executed direction. Broker-confirmed EA reversal, later
policy override and order execution provenance are separate questions.

The JSON lists counts for all closed trades and separately for the
`decision_samples.direction` mismatches. These should be checked against
the prior 120 mismatches, but no match is assumed in advance. The CSV
contains one row per trade with recorded direction fields and UTC
chronology. Missing final_decision remains missing (no guess).

The offset 10800 represents broker UTC+03:00 for the verified period only;
the user-observed timestamps are consistent with that offset, not
independent historical proof of every broker clock interval.
All database access is read-only; no live EA/model/risk changes.
