# Confirmed reversal execution

This is an executable MAIN entry route, not a shadow observer. It is disabled
by default (`RAMON_REVERSAL_LIVE_ENABLED=0`). No EA changes are required.

It can propose BUY/SELL only when the unmodified base result is blocked by
`trend_conflict`, both full model-path and intrabar confirmations agree,
edge passes its existing minimum, strength is at least the ordinary 0.20
threshold, and micro extension is below the existing maximum. Weak-entry
exceptions do not lower this route's strength floor. Manual passes and an
active ensemble do not activate it.

The route replaces only the opposing TREND/PULLBACK/BREAKOUT/BREAKOUT_RETEST
directional veto. Hazard, range and uncertain market states remain blocked.
The subsequent cent-account independent direction/timing gates, MOMENT and
FinBERT vetoes, and terminal news/account/risk/position/entry-limit checks remain
authoritative. Failure to persist its decision blocks entry. Normal MAIN
SL/TP and position management remain in use.

Decision reason: `confirmed_countertrend_reversal`.
Recorded strategy: `confirmed-reversal-v1`.
Recorded route: `CONFIRMED_REVERSAL`, with original market route retained.

## User activation

Enabling this setting may cause the connected EA to send real orders. The user
must perform this final action. From the project directory, set this line in
the local `.env` file, preserving other settings:

```
RAMON_REVERSAL_LIVE_ENABLED=1
```

Then rebuild and load the model service:

```
docker compose up -d --build model
```

For updated monitor reason labels, reload the dashboard as well:

```
docker compose up -d --no-build dashboard market-dashboard
```

Check `/health` for `reversal_live_enabled: true` and examine recorded decisions.
An enabled route still may WAIT when its requirements fail. To stop proposing
new entries through this route, set the variable to `0` and recreate the model
service. Disabling it does not close existing positions.

## Validation limits

Tests use synthetic data and isolated local HTTP services; no orders are sent.
The retrospective morning examples motivated this route, but one day's replay
does not establish profitability. Independent days and demo execution are
still needed to measure drawdown, costs and actual exit management. No live
setting was enabled during implementation.
