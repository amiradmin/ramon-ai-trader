# Ramon Selection Failure Lab

This research-only diagnostic compares M15 signal bars that Ramon selected for a
real trade with bars that Ramon observed but did not select.

It is designed to answer: which stored features distinguish selected bars, and
within selected bars, are larger feature values associated with better or worse
replayed outcomes?

The lab reports two views for both full history and the final chronological
holdout:

- selection feature shift: selected mean vs nonselected mean, plus standardized
  difference (`stdΔ`);
- selected outcome relation: correlation between each feature and replayed R,
  plus mean R for the lowest and highest feature quartiles.

It automatically flattens numeric values from stored regime, entry, meta, news
and Chronos base-decision telemetry. Settings and shadow-only metadata are
excluded from the generic flattening so configuration constants do not dominate
the report.

## Run

```bash
docker compose --profile tools run --rm tools \
  -m ramon.selection_failure_lab \
  --db /data/ramon_history.sqlite3 \
  --symbol XAUUSD_l \
  --max-bars 24 \
  --holdout 0.30 \
  --top 20 \
  --min-coverage 0.70
```

A large standardized selection shift is descriptive, not proof that a threshold
should change. Likewise, feature/outcome correlation can be regime-dependent and
must be validated out of sample before any live promotion.

This module has no live effect.
