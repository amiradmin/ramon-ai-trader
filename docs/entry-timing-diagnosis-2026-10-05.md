# Entry timing diagnosis — 2026-10-05

Local analysis: http://127.0.0.1:8013/api/analysis?stage=entry_timing

Exact sample replayed offline: `023d8ae5e6c842fe`. Stored inputs and forecast
reproduce `WAIT / trend_conflict`. No trading requests were sent.

The independent market direction was BUY (score 2), while the forecast's
dominant edge was SELL. BUY timing was ready, but this did not confirm SELL
timing or authorize an order. The preceding 12 completed M15 candles rose
3.345 ATR, exceeding the 3 ATR countertrend gate. Signal strength was
0.1256, below the ordinary 0.20 threshold and reversal override's 0.70
threshold; model-path and signal-side intrabar confirmations also failed.

The replay's broker quote time was 1791200335, last completed M15 open
1791198900, and latest M1 open 1791200280 (55 seconds old).
`Market.validate_quote_context` passed. The forecast cache keys on completed
closing prices and horizon; an unchanged forecast within the M15 context
is expected, while edges update against current Bid/Ask.

The monitor previously showed raw market-direction timing readiness as
“آمادهٔ ورود”, even when that direction opposed the model signal.
It now shows the signal direction and market-direction timing separately,
and only reports signal readiness when the directions agree. A timing pass
explicitly does not imply final order approval. Trading rules are unchanged.

Validation: 56 tests passed across monitor, decision and trend-conflict tests,
including four new timing cases. Two existing monitor tests fail because
they expect `read_only=True` whereas HEAD already returns False for the
monitor's control-enabled surface. These failures are unrelated to this edit.

The running dashboard module was updated and only the dashboard restarted.
The corrected fields were verified at the analysis endpoint. Model and
trade-outbox containers were not restarted. Source is saved in the repository;
a future container recreation from the old image requires rebuilding the
image to retain this update.

Latest observed sample at 08:43:20 UTC (12:13:20 Tehran):
`62ca511392b94ce5`, WAIT / late_entry_extension; market direction NEUTRAL,
BUY edge -0.3331, SELL edge -0.5069. The inferred dominant BUY direction
is only the less negative edge, not a valid purchase signal.
