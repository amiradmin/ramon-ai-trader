# Frozen Trend Pullback forward check

The research inputs are frozen in
`backend/django_bridge/ea_bridge/research_presets/frozen-trend-pullback-forward-2026-11-23.json`.
They match the user's October 22–November 22 export: XAUUSD_l, 1,000 virtual
account units, 20-point fixed spread, 0.5% risk, 0.1 ATR stop buffer, 1.2
target ratio, minimum opportunity score 0.55, minimum entry quality 0.5,
1-point slippage per side and 1 account unit per lot round-trip commission.
The earlier export had only three closed trades at this balance. This
configuration is a research hypothesis, not a verified live policy.

The forward command never includes any candle dated November 22 or earlier
in the evaluated period. It may use prior candles only for indicator warm-up.
It does not change the strategy, training models, live account or MT5 orders.
It waits until at least 1,000 new M15 candles are available and excludes the
current UTC day. It reports only descriptive results and data-quality flags;
do not tune the fixed parameters using these results and then call the same
window an untouched test.

The `litefinance-mt5-live-export-20261122` source was imported from a static
MT5 M1 CSV ending on November 20. New data does not appear by itself. Export
new XAUUSD_l M1 candles from the same LiteFinance server and follow
`docs/operations/mt5-export-import.md` for an import dry run and UTC offset
checks. Import only after inspecting the dry-run summary; existing candles
are immutable and mismatches abort the transaction. Then run:

```sh
docker compose exec django python manage.py evaluate_frozen_forward
```

`WAITING` means there is not enough new data yet. A JSON summary reports the
new date range, counts, P&L, drawdown, gaps and fingerprint once the minimum
is met. The command reads stored M15 only and does not place trades. The
reported spread and execution costs remain assumptions rather than verified
historical broker fills. Subsequent strategy-code or data-source changes
must be recorded before comparing reports.
