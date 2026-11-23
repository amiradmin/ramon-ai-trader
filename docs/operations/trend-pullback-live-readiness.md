# TREND_PULLBACK real-market readiness

Ramon already implements real strategy `OPEN` orders through `strategy_worker.worker_cycle` → `decision.evaluate_payload` → persisted risk verdict → `command_producer.create_open_command` → MT5 EA command polling. Research replay/ARIMA outputs **must never** be sent to this path. No new market order is created by installing this change.

This change blocks real M15 strategy evaluations unless the **latest 128 stored closed candles** have exact 15-minute UTC timestamp alignment and no gaps or duplicates. The last candle must have closed and be fresh. This deliberately blocks entries during history outages; it does not backfill candles or claim any gain in profitability. The monitor will show `M15_WARMUP`, `M15_STALE`, `M15_OFF_GRID`, or `M15_GAPS_OR_DUPLICATES` as appropriate.

## Existing live entry conditions
- The configured real-cent feed must exactly match server, account, symbol, instance, allowed currency, hedging mode, and ticket/magic EA execution profile.
- Current quote and terminal telemetry must be fresh, trading allowed, and strategy automatic control on.
- Global strategy-container gate and both EA command creation/execution gates must be explicitly enabled by the operator. Never turn these gates on merely because a backtest performs well.
- A completed TREND_PULLBACK M15 signal must be approved by opportunity, entry-timing, and risk-management stages.
- The command producer rechecks account/balance/equity scope, quote, stop/target, at most 0.5% per-trade reserved risk, configured strategy cap, combined portfolio exposure, ownership, duplicate orders, and the news lock.
- MT5 must report actual fill or rejection. A queued command is not an executed trade.

## Local test

```sh
docker compose exec django python manage.py test ea_bridge.test_live_m15_quality ea_bridge.tests_strategy_workers
docker compose exec django python manage.py audit_trading_readiness
docker compose exec django python manage.py run_strategy_worker --setup TREND_PULLBACK --once
docker compose logs --tail=100 trend-pullback
```

Inspect dashboard state, `command_created`, command ID and eventual EA execution result. The live strategy is **not** switched on by this PR and no order was placed here. Carefully inspect the position and symbol economics in MT5 before opting in to real-money automation. Check that the EA source/executable corresponds to the server-side `ticket-magic-v1` profile.

## Next steps before dynamic SL/TP goes live

The previous research PR (#208) is not part of live execution. In particular, ATR trailing and hybrid exits are not approved live policies. The existing optional `RAMON_REAL_CENT_PROFIT_PROTECTION_ENABLED` rule only moves an owned position SL toward entry after +1R and preserves TP. Verify broker stop/freeze distances and modification result in a demo or isolated cent-account run before enabling it. Record rejected fills and execution slippage; run walk-forward tests and assess drawdown. No forecast observer can override the hard risk gates.
