# TREND_PULLBACK position management — phase 1

The independent `trend-pullback` worker now refreshes a **read-only**
`StrategyWorkerControl.latest.position_management` snapshot on every worker
cycle with ready telemetry (nominally every five seconds), even when entry
automation is paused.

The snapshot contains only positions which have both the strategy's magic
number and a single broker-ticket-matched, executed, account-scoped OPEN
command. It exposes current executable-price R (BUY uses bid; SELL uses ask),
original risk, current SL, fixed TP, candidate breakeven SL, and the existing
protection command state. Invalid or stale telemetry yields no position rows.

**No new MT5 action is enabled by this change.** The pre-existing +1R
breakeven command path remains gated by all three existing settings:
`RAMON_REAL_CENT_PROFIT_PROTECTION_ENABLED`,
`RAMON_EA_COMMAND_CREATION_ENABLED`, and
`RAMON_EA_COMMAND_EXECUTION_ENABLED`. Leave them disabled until controlled
replay and paper verification. The legacy protection policy is unchanged.

## Release sequence

1. Verify snapshot accuracy with fresh hedging-mode EA telemetry and position tickets.
2. Compare baseline exits against +1R breakeven on independent out-of-sample replay,
   including spread, slippage, commissions, and rejected modifications.
3. Paper test the existing execution/ack/reconciliation flow before any live enablement.
4. Add ATR trailing stop, time exit, and reversal logging as independent experiments,
   default OFF; dynamic TP remains fixed until separately validated.

Safety notes: no historical maximum is inferred on restart, broker-confirmed
SL/TP remains authoritative, and an uncertain modification must not be blindly
retried. A protective stop at entry does **not** guarantee zero net loss after
fees and slippage.
