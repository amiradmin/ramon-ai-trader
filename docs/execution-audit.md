# Ramon Execution Audit

This audit checks two things that can invalidate or distort strategy validation:

1. entry-time alignment between the saved decision snapshot and the real executed trade;
2. Bid/Ask side semantics in M15 replay, especially for SELL exits.

The live EA sends completed M15 OHLC from MT5 `CopyRates` and sends the current
Bid/Ask quote separately. The history database stores OHLC plus `spread_points`,
but it does not store a separate Ask OHLC series.

The audit therefore reports:

- quote-time coverage;
- whether actual trade-open timestamps occur after the saved decision/quote;
- signal-bar age at the real fill;
- historical spread coverage;
- an estimated symbol point from saved spread / spread-points pairs;
- a SELL sensitivity test that compares raw OHLC exit classification with an
  Ask proxy formed as `raw OHLC + spread_points * point`;
- whether actual entry fill price is present in persisted trade telemetry.

Run:

```bash
docker compose --profile tools run --rm tools \
  -m ramon.execution_audit \
  --db /data/ramon_history.sqlite3 \
  --symbol XAUUSD_l \
  --max-bars 24
```

Important limitations:

- the Ask proxy is a diagnostic, not broker-exact tick replay;
- bars without spread telemetry cannot be included in the adjusted SELL comparison;
- exact entry slippage cannot be audited until the actual fill price is persisted;
- no live trading behavior is changed by this tool.
