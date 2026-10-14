# Ramon direction-conflict research

This prototype is **research-only**. It does not influence V2/V3, send orders, or promote models.

The PostgreSQL report requires the existing runtime `RAMON_PG_TRAINING_DSN` and uses a read-only transaction. Execute from project source:

```bash
PYTHONPATH=src python -m ramon.direction_conflict_report
```

Review aggregate `WAIT_CONFLICT`, `DIRECTION_OVERRIDE`, and `NO_SNAPSHOT_DIRECTION` categories by source, net R, and count entering within 90 seconds of the recorded quote.

**Interpretation:** the result is observational and contains only executed trades. It does not estimate the profitability of skipped signals or establish that restricting WAIT conflicts would improve live trading.

Before any live change: reconcile every sample with the actual decision ID and EA entry reason; benchmark a proposed filter on purged, unseen days; compare net R, trade frequency and drawdown to the incumbent; require explicit approval for production change. Preserve manual trading control.
